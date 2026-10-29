"""
Ingest Pipeline Orchestrator.

Processes a week_dir of WhatsApp exports:
1. Parse _chat.txt
2. OCR bill photos (Llama Vision)
3. Parse PDF rate lists
4. Transcribe voice notes (Whisper)
5. Parse UPI screenshots (Llama Vision)
6. Build/update distributor knowledge cards
7. Safety check all messages
"""

from __future__ import annotations
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from harness.config import HarnessConfig
from harness.agents.llama_client import LlamaClient
from harness.knowledge.store import KnowledgeStore
from harness.knowledge.models import (
    RateList, RateQuote, Bill, Scheme, Payment, SchemeType, SourceType,
    AuthorizedPerson, Statement, StatementEntry, DistributorCard,
)
from harness.ingest.chat_parser import parse_chat_file, ChatMessage
from harness.ingest.bill_ocr import extract_bill_from_image, extract_statement_from_image
from harness.ingest.pdf_parser import parse_rate_list
from harness.ingest.voice_stt import transcribe_voice_note
from harness.ingest.upi_parser import extract_upi_payment
from harness.ingest.safety import SafetyChecker
from harness.ingest.parse_utils import parse_finite_number, parse_iso_date

logger = logging.getLogger(__name__)


class IngestPipeline:
    """Orchestrates the full ingestion of a week's WhatsApp exports."""

    def __init__(self, config: HarnessConfig, store: KnowledgeStore):
        self.config = config
        self.store = store
        self.llama = LlamaClient(config.llama)
        self.safety = SafetyChecker(self.llama)
        self._recent_product_by_distributor: dict[str, str] = {}
        self._stats = {
            "messages_parsed": 0,
            "bills_extracted": 0,
            "rate_lists_parsed": 0,
            "voice_notes_transcribed": 0,
            "upi_payments_extracted": 0,
            "schemes_found": 0,
            "safety_blocks": 0,
        }

    def ingest(self, week_dir: str) -> dict:
        """Ingest all files from a week directory.

        Args:
            week_dir: Path to the week directory containing WhatsApp exports.

        Returns:
            Stats about the ingestion.
        """
        week_path = Path(week_dir).resolve()
        if not week_path.exists():
            raise FileNotFoundError(f"Week directory not found: {week_path}")

        logger.info(f"═══ Starting ingestion from {week_path} ═══")

        # Step 1: Find and parse _chat.txt
        chat_files = list(dict.fromkeys(
            list(week_path.glob("*_chat.txt")) + list(week_path.glob("*chat*.txt"))
        ))
        if not chat_files:
            # Also look for any .txt file that might be a chat
            chat_files = list(week_path.glob("*.txt"))

        messages: list[ChatMessage] = []
        for chat_file in sorted(chat_files):
            msgs = parse_chat_file(chat_file)
            messages.extend(msgs)
            self._stats["messages_parsed"] += len(msgs)

        logger.info(f"Parsed {len(messages)} chat messages")

        # Quarantine unsafe messages before they reach the distributor-identification
        # prompt or any attachment parser. Also mark their attachments consumed so
        # the loose-file fallback cannot silently process them afterward.
        safe_messages: list[ChatMessage] = []
        blocked_attachments: set[str] = set()
        for msg in messages:
            safety_result = self.safety.check_message(msg.text, msg.is_forwarded)
            if safety_result["is_safe"]:
                safe_messages.append(msg)
                continue
            self._stats["safety_blocks"] += 1
            if msg.attachment:
                blocked_attachments.add(msg.attachment)
            logger.warning(f"🛑 Blocked unsafe message from {msg.sender} "
                           f"(line {msg.line_number}): {safety_result['details']}")

        # Step 2: Identify distributors and their contact persons from chat context
        self._identify_distributors(safe_messages, week_path)

        # Step 3: Process attachments found in messages
        processed_files: set[str] = set(blocked_attachments)

        for msg in safe_messages:
            # Process attachments
            if msg.has_attachment and msg.attachment not in processed_files:
                attachment_path = week_path / msg.attachment
                if not attachment_path.exists():
                    # Try alternate patterns
                    candidates = list(week_path.glob(f"*{Path(msg.attachment).stem}*"))
                    if candidates:
                        attachment_path = candidates[0]
                    else:
                        logger.warning(f"Attachment not found: {msg.attachment}")
                        continue

                self._process_attachment(
                    str(attachment_path),
                    msg.attachment_type,
                    msg.sender,
                    msg,
                )
                processed_files.add(msg.attachment)

            self._record_product_context(msg)
            # Process scheme offers in chat text
            self._process_rate_quote(msg.text, msg.sender, msg.timestamp,
                                     SourceType.CHAT_MESSAGE,
                                     f"_chat.txt:L{msg.line_number}")
            if self._looks_like_scheme(msg.text):
                self._process_scheme_message(msg)

        # Step 4: Process loose files (not referenced in chat)
        self._process_loose_files(week_path, processed_files)

        # Step 5: Link related data (match payments to bills, etc.)
        self._link_data()
        self._deduplicate_state_records()

        # Save state
        self.store.save()
        self.store.update_meta("last_ingest", datetime.now().isoformat())
        self.store.update_meta("last_week_dir", str(week_path))
        self.store.save()

        logger.info(f"═══ Ingestion complete ═══")
        logger.info(f"Stats: {self._stats}")
        return self._stats

    def _identify_distributors(self, messages: list[ChatMessage], week_path: Path):
        """Identify distributors and authorized persons from chat context."""
        # Use Llama to identify distributors from chat messages
        senders: dict[str, list[str]] = {}
        for msg in messages[:100]:  # Sample first 100 messages
            if not msg.is_system:
                senders.setdefault(msg.sender, []).append(msg.text[:200])

        if not senders:
            return

        # Try to identify from chat context
        sample_text = "\n".join(
            f"{sender}: {texts[0][:100]}"
            for sender, texts in list(senders.items())[:10]
        )

        try:
            prompt = f"""From this WhatsApp chat, identify:
1. The shop owner (usually sends the most messages or is the one paying)
2. Distributor salesmen (they send bills, rate lists, offer schemes)

Chat sample:
{sample_text}

Files in directory: {[f.name for f in week_path.iterdir()][:20]}

Return JSON:
{{
    "owner": "owner name",
    "distributors": [
        {{
            "code": "2-3 letter code like GT, SP",
            "name": "full distributor name",
            "salesmen": ["salesman names"],
            "identified_from": "how you identified this"
        }}
    ]
}}"""

            result = self.llama.extract_json(prompt)

            for dist_info in result.get("distributors", []):
                code = dist_info.get("code", "UNK")
                name = dist_info.get("name", code)
                card = self.store.get_or_create_distributor(code, name)

                for salesman in dist_info.get("salesmen", []):
                    if not any(ap.name.lower() == salesman.lower()
                              for ap in card.authorized_persons):
                        card.authorized_persons.append(
                            AuthorizedPerson(name=salesman, role="salesman")
                        )

            logger.info(f"Identified {len(result.get('distributors', []))} distributors")

        except Exception as e:
            logger.warning(f"Distributor identification via Llama failed: {e}, using heuristic identification")
            for sender in senders.keys():
                code = self._guess_distributor_code("", sender)
                if code != "UNK":
                    card = self.store.get_or_create_distributor(code, sender)
                    if not any(ap.name.lower() == sender.lower() for ap in card.authorized_persons):
                        card.authorized_persons.append(
                            AuthorizedPerson(name=sender, role="salesman", can_offer_schemes=True)
                        )
            if not self.store.list_distributors():
                self.store.get_or_create_distributor("UNK", "Unknown Distributor")

    def _process_attachment(
        self,
        file_path: str,
        file_type: Optional[str],
        sender: str,
        msg: ChatMessage,
    ):
        """Process a single attachment file."""
        path = Path(file_path)
        logger.info(f"Processing attachment: {path.name} (type={file_type}, sender={sender})")

        if file_type == "image":
            self._process_image(file_path, sender, msg)
        elif file_type == "pdf":
            self._process_pdf(file_path, sender, msg)
        elif file_type == "voice":
            self._process_voice(file_path, sender, msg)

    def _process_image(self, image_path: str, sender: str, msg: ChatMessage):
        """Process an image attachment (bill photo or UPI screenshot)."""
        filename = Path(image_path).name.lower()

        # Determine if this is a UPI screenshot or a bill
        is_upi = any(k in filename for k in ["upi", "payment", "gpay", "phonepe", "paytm"])

        # Also check chat context
        msg_lower = msg.text.lower()
        if any(k in msg_lower for k in ["payment", "paid", "upi", "bhej diya", "transfer"]):
            is_upi = True

        # Check if it looks like a statement
        is_statement = any(k in filename for k in ["statement", "outstanding", "balance", "dues"])
        if any(k in msg_lower for k in ["statement", "outstanding", "baki", "dues"]):
            is_statement = True

        dist_code = self._guess_distributor_code(filename, sender)

        if is_upi:
            payment = extract_upi_payment(self.llama, image_path, dist_code)
            if payment:
                card = self.store.get_or_create_distributor(dist_code)
                if not any(existing.payment_id == payment.payment_id for existing in card.payments):
                    card.payments.append(payment)
                    self._stats["upi_payments_extracted"] += 1
        elif is_statement:
            stmt_data = extract_statement_from_image(self.llama, image_path)
            if stmt_data:
                self._process_statement_data(stmt_data, dist_code, image_path)
        else:
            bill = extract_bill_from_image(self.llama, image_path, dist_code)
            if bill:
                if bill.raw_text and not self.safety.check_message(bill.raw_text)["is_safe"]:
                    self._stats["safety_blocks"] += 1
                    logger.warning(f"Quarantined bill content containing unsafe instructions: {Path(image_path).name}")
                    return
                # Update distributor code from the bill
                if bill.distributor_code != "UNK":
                    dist_code = bill.distributor_code
                card = self.store.get_or_create_distributor(dist_code)
                # Avoid duplicate bills
                existing_index = next((i for i, old in enumerate(card.bills)
                                       if old.bill_id == bill.bill_id), None)
                if existing_index is None:
                    card.bills.append(bill)
                    self._stats["bills_extracted"] += 1
                else:
                    previous = card.bills[existing_index]
                    bill.is_paid = previous.is_paid
                    bill.payment_ref = previous.payment_ref
                    card.bills[existing_index] = bill
                    logger.info(f"Refreshed previously ingested bill: {bill.bill_id}")

    def _process_pdf(self, pdf_path: str, sender: str, msg: ChatMessage):
        """Process a PDF attachment (rate list)."""
        dist_code = self._guess_distributor_code(Path(pdf_path).name, sender)

        rate_list = parse_rate_list(self.llama, pdf_path, dist_code)
        if rate_list:
            card = self.store.get_or_create_distributor(rate_list.distributor_code)

            # Check for superseded rate lists
            for existing_rl in card.rate_lists:
                if (existing_rl.effective_date < rate_list.effective_date and
                        existing_rl.superseded_by is None):
                    existing_rl.superseded_by = rate_list.version

            existing_index = next((i for i, old in enumerate(card.rate_lists)
                if old.source_file == rate_list.source_file
                and old.version == rate_list.version
                and old.effective_date == rate_list.effective_date), None)
            if existing_index is None:
                card.rate_lists.append(rate_list)
                self._stats["rate_lists_parsed"] += 1
            else:
                card.rate_lists[existing_index] = rate_list

    def _process_voice(self, audio_path: str, sender: str, msg: ChatMessage):
        """Process a voice note attachment."""
        result = transcribe_voice_note(
            audio_path,
            language=self.config.whisper.language,
            model_size=self.config.whisper.model_size,
        )

        if result["text"]:
            self._stats["voice_notes_transcribed"] += 1
            logger.info(f"Voice note from {sender}: '{result['text'][:100]}...'")

            if not self.safety.check_message(result["text"])["is_safe"]:
                self._stats["safety_blocks"] += 1
                logger.warning(f"Quarantined voice note containing unsafe instructions: {Path(audio_path).name}")
                return

            self._process_rate_quote(
                result["text"], sender, msg.timestamp, SourceType.VOICE_NOTE,
                f"voice:{Path(audio_path).name}",
            )

            # Check for scheme offers
            if self._looks_like_scheme(result["text"]):
                dist_code = self._guess_distributor_code("", sender)
                card = self.store.get_distributor(dist_code)
                if card is None or not self._can_offer_schemes(card, sender):
                    logger.info(f"Ignoring scheme-like voice note from unauthorized sender: {sender}")
                    return

                try:
                    scheme_data = self.llama.parse_hinglish_scheme(
                        result["text"], speaker=sender
                    )
                    self._contextualize_scheme(scheme_data, dist_code)

                    if scheme_data.get("is_scheme"):
                        scheme = Scheme(
                            scheme_id=f"SCH-{msg.line_number}-{sender}",
                            distributor_code=dist_code,
                            product=scheme_data.get("product", "general"),
                            scheme_type=SchemeType(scheme_data.get("scheme_type", "one_time")),
                            buy_qty=scheme_data.get("buy_qty"),
                            free_qty=scheme_data.get("free_qty"),
                            discount_pct=scheme_data.get("discount_pct"),
                            agreed_by=sender,
                            agreed_date=msg.timestamp.date() if msg.timestamp else None,
                            source_type=SourceType.VOICE_NOTE,
                            source_ref=f"voice:{Path(audio_path).name}",
                            raw_text=result["text"],
                        )

                        card = self.store.get_or_create_distributor(dist_code)
                        if self._can_offer_schemes(card, sender):
                            existing_index = next((i for i, old in enumerate(card.schemes)
                                                   if old.scheme_id == scheme.scheme_id), None)
                            if existing_index is None:
                                card.schemes.append(scheme)
                                self._stats["schemes_found"] += 1
                            else:
                                card.schemes[existing_index] = scheme
                            logger.info(f"Scheme found in voice note: {scheme_data}")
                except Exception as e:
                    logger.warning(f"Failed to parse scheme from voice note: {e}")

    def _process_scheme_message(self, msg: ChatMessage):
        """Process a chat message that looks like a scheme offer."""
        dist_code = self._guess_distributor_code("", msg.sender)
        card = self.store.get_distributor(dist_code)
        if card is None or not self._can_offer_schemes(card, msg.sender):
            logger.info(f"Ignoring scheme-like chat from unauthorized sender: {msg.sender}")
            return

        try:
            scheme_data = self.llama.parse_hinglish_scheme(msg.text, speaker=msg.sender)
            self._contextualize_scheme(scheme_data, dist_code)

            if scheme_data.get("is_scheme"):
                scheme = Scheme(
                    scheme_id=f"SCH-{msg.line_number}-{msg.sender}",
                    distributor_code=dist_code,
                    product=scheme_data.get("product", "general"),
                    scheme_type=SchemeType(scheme_data.get("scheme_type", "one_time")),
                    buy_qty=scheme_data.get("buy_qty"),
                    free_qty=scheme_data.get("free_qty"),
                    discount_pct=scheme_data.get("discount_pct"),
                    agreed_by=msg.sender,
                    agreed_date=msg.timestamp.date() if msg.timestamp else None,
                    source_type=SourceType.CHAT_MESSAGE,
                    source_ref=f"_chat.txt:L{msg.line_number}",
                    raw_text=msg.text,
                )

                card = self.store.get_or_create_distributor(dist_code)
                if self._can_offer_schemes(card, msg.sender):
                    existing_index = next((i for i, old in enumerate(card.schemes)
                                           if old.scheme_id == scheme.scheme_id), None)
                    if existing_index is None:
                        card.schemes.append(scheme)
                        self._stats["schemes_found"] += 1
                    else:
                        card.schemes[existing_index] = scheme
        except Exception as e:
            logger.warning(f"Failed to parse scheme from chat: {e}")

    def _process_rate_quote(self, text: str, sender: str, timestamp: datetime,
                            source_type: SourceType, source_ref: str):
        """Capture explicit named-salesman prices so later disagreement is visible."""
        normalized = str.maketrans("०१२३४५६७८९", "0123456789")
        clean_text = text.translate(normalized)
        if not re.search(r"\b(rate|price|bhav|daam)\b", clean_text, re.IGNORECASE):
            return
        match = re.search(
            r"\b(?:rate|price|bhav|daam)\s*(?:(?:hai|is|ab|at|=|:|ka)\s*)?"
            r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)",
            clean_text, re.IGNORECASE,
        )
        if not match:
            match = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", clean_text, re.IGNORECASE)
        if not match:
            return

        dist_code = self._guess_distributor_code("", sender)
        card = self.store.get_distributor(dist_code)
        if card is None or not card.is_authorized(sender):
            return

        searchable = clean_text.casefold()
        products = {entry.product: entry for rl in card.rate_lists for entry in rl.entries}
        product_scores = []
        stop_words = {"carton", "product", "price", "rate", "free", "item", "bag", "piece", "packet", "unit"}
        for name in products:
            tokens = {word for word in re.findall(r"[a-z0-9]+", name.casefold())
                      if len(word) >= 4 and word not in stop_words}
            score = len(tokens.intersection(re.findall(r"[a-z0-9]+", searchable)))
            if name.casefold() in searchable:
                score += len(tokens) + 1
            if score:
                product_scores.append((score, name))
        product_scores.sort(key=lambda pair: (-pair[0], pair[1].casefold()))
        product = (product_scores[0][1]
                   if product_scores and (len(product_scores) == 1
                       or product_scores[0][0] > product_scores[1][0]) else None)
        if not product:
            return
        try:
            rate = float(match.group(1).replace(",", ""))
        except ValueError:
            return
        if rate <= 0:
            return

        quote = RateQuote(
            product=product,
            rate=rate,
            agreed_by=sender,
            agreed_date=timestamp.date(),
            source_type=source_type,
            source_ref=source_ref,
        )
        if not any(q.source_ref == source_ref and q.product.casefold() == product.casefold()
                   and abs(q.rate - rate) < 0.01 for q in card.rate_quotes):
            card.rate_quotes.append(quote)
            logger.info(f"Recorded explicit rate quote for {product}: ₹{rate} from {sender}")

    def _record_product_context(self, msg: ChatMessage):
        """Remember a uniquely named product mentioned in this supplier thread."""
        dist_code = self._guess_distributor_code("", msg.sender)
        card = self.store.get_distributor(dist_code)
        if card is None:
            return
        text = msg.text.casefold()
        names = {entry.product for rl in card.rate_lists for entry in rl.entries}
        matches = []
        for product in names:
            product_lower = product.casefold()
            distinctive = [word for word in re.findall(r"[a-z0-9]+", product_lower)
                           if len(word) >= 5]
            if product_lower in text or (distinctive and any(word in text for word in distinctive)):
                matches.append(product)
        if len(matches) == 1:
            self._recent_product_by_distributor[dist_code] = matches[0]

    def _contextualize_scheme(self, scheme_data: dict, dist_code: str):
        product = str(scheme_data.get("product", "")).strip().casefold()
        if product in ("", "general", "all", "any"):
            contextual_product = self._recent_product_by_distributor.get(dist_code)
            if contextual_product:
                scheme_data["product"] = contextual_product

    def _process_statement_data(
        self, stmt_data: dict, dist_code: str, source_file: str,
    ):
        """Process raw statement data into a Statement object."""
        try:
            statement_date = parse_iso_date(stmt_data.get("statement_date"))
            if statement_date is None:
                logger.warning(
                    f"Ignoring statement {Path(source_file).name}: missing or invalid "
                    "statement date"
                )
                return

            entries = []
            for entry in stmt_data.get("entries", []):
                bill_date = parse_iso_date(entry.get("bill_date"))
                amount = parse_finite_number(entry.get("amount"))
                bill_id = str(entry.get("bill_id", "")).strip()
                if bill_date is None or amount is None or amount < 0 or not bill_id:
                    logger.warning(
                        f"Skipping invalid statement entry in {Path(source_file).name}: "
                        f"bill_id={bill_id!r}, bill_date={entry.get('bill_date')!r}, "
                        f"amount={entry.get('amount')!r}"
                    )
                    continue
                status = str(entry.get("status", "unknown")).strip().lower()
                if status not in {"due", "partial", "paid"}:
                    status = "unknown"
                entries.append(StatementEntry(
                    bill_id=bill_id,
                    bill_date=bill_date,
                    amount=amount,
                    status=status,
                ))

            stmt = Statement(
                distributor_code=dist_code,
                statement_date=statement_date,
                source_file=Path(source_file).name,
                entries=entries,
            )

            card = self.store.get_or_create_distributor(dist_code)
            if not any(old.source_file == stmt.source_file for old in card.statements):
                card.statements.append(stmt)
        except Exception as e:
            logger.warning(f"Failed to process statement data: {e}")

    def _process_loose_files(self, week_path: Path, processed: set[str]):
        """Process files in the week directory not referenced in chat."""
        for f in sorted(week_path.iterdir()):
            if f.name in processed or f.suffix.lower() == ".txt":
                continue

            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                logger.info(f"Processing loose image: {f.name}")
                dummy_msg = ChatMessage(
                    line_number=0, timestamp=datetime.now(),
                    sender="unknown", text="", raw_line="",
                )
                self._process_image(str(f), "unknown", dummy_msg)
                processed.add(f.name)

            elif f.suffix.lower() == ".pdf":
                logger.info(f"Processing loose PDF: {f.name}")
                dummy_msg = ChatMessage(
                    line_number=0, timestamp=datetime.now(),
                    sender="unknown", text="", raw_line="",
                )
                self._process_pdf(str(f), "unknown", dummy_msg)
                processed.add(f.name)

            elif f.suffix.lower() in {".opus", ".mp3"}:
                logger.info(f"Processing loose audio: {f.name}")
                dummy_msg = ChatMessage(
                    line_number=0, timestamp=datetime.now(),
                    sender="unknown", text="", raw_line="",
                )
                self._process_voice(str(f), "unknown", dummy_msg)
                processed.add(f.name)

    def _link_data(self):
        """Link related data across distributors (e.g., match payments to bills)."""
        for card in self.store.list_distributors():
            # Match payments to bills by amount and approximate date
            for payment in card.payments:
                if payment.matched_bill_id or not payment.is_verified:
                    continue  # Already matched

                for bill in sorted(card.bills, key=lambda b: (b.bill_date, b.bill_id)):
                    if (abs(bill.grand_total - payment.amount) < 1.0
                            and abs((bill.bill_date - payment.payment_date).days) <= 3
                            and not bill.is_paid):
                        payment.matched_bill_id = bill.bill_id
                        bill.is_paid = True
                        bill.payment_ref = payment.upi_ref
                        logger.info(f"Matched payment ₹{payment.amount} to bill {bill.bill_id}")
                        break

    def _deduplicate_state_records(self):
        """Collapse duplicate records left by earlier or repeated ingestion runs."""
        for card in self.store.list_distributors():
            card.bills = self._unique_by(card.bills, lambda x: x.bill_id)
            card.rate_lists = self._unique_by(
                card.rate_lists,
                lambda x: (x.source_file, x.version, x.effective_date),
            )
            card.rate_quotes = self._unique_by(
                card.rate_quotes,
                lambda x: (x.source_ref, x.product.casefold(), round(x.rate, 2)),
            )
            card.schemes = self._unique_by(card.schemes, lambda x: x.scheme_id)
            card.schemes = [scheme for scheme in card.schemes
                if scheme.source_type not in (SourceType.CHAT_MESSAGE, SourceType.VOICE_NOTE)
                or any(person.name.casefold() == scheme.agreed_by.casefold()
                       and person.can_offer_schemes for person in card.authorized_persons)]
            card.payments = self._unique_by(card.payments, lambda x: x.payment_id)
            card.statements = self._unique_by(card.statements, lambda x: x.source_file)

    @staticmethod
    def _unique_by(records, key):
        result = {}
        for record in records:
            result[key(record)] = record
        return list(result.values())

    def _looks_like_scheme(self, text: str) -> bool:
        """Quick heuristic to check if text contains a scheme offer."""
        scheme_keywords = [
            r"free", r"muft", r"gratis",
            r"pe ", r" pe",  # "X pe Y" pattern
            r"discount", r"chhut",
            r"scheme", r"offer",
            r"buy.*get", r"lo.*milega",
            r"carton.*free", r"\+1", r"\+2",
            r"hamesha", r"standing",
        ]
        text_lower = text.lower()
        return any(re.search(kw, text_lower) for kw in scheme_keywords)

    @staticmethod
    def _can_offer_schemes(card: DistributorCard, sender: str) -> bool:
        """Only accept schemes from a contact authorized on the contract card."""
        normalized = sender.casefold().strip()
        return any(
            person.can_offer_schemes and person.name.casefold().strip() == normalized
            for person in card.authorized_persons
        )

    def _guess_distributor_code(self, filename: str, sender: str) -> str:
        """Guess distributor code from filename or sender name."""
        # 1. Match sender keywords
        sender_upper = sender.upper()
        if "GANGA" in sender_upper or "RAMESH" in sender_upper:
            return "GT"
        if "SHARMA" in sender_upper or "MANOJ" in sender_upper:
            return "SP"
        if "BENGAL" in sender_upper or "AMIT" in sender_upper:
            return "BC"
        if "KASHI" in sender_upper or "VIJAY" in sender_upper:
            return "KP"
        if "BANARAS" in sender_upper or "RAJESH" in sender_upper:
            return "BW"

        # 2. Check known distributors in store
        for card in self.store.list_distributors():
            if card.code.lower() in filename.lower():
                return card.code
            for ap in card.authorized_persons:
                if ap.name.lower() in sender.lower() or sender.lower() in ap.name.lower():
                    return card.code

        # 3. Check filename token delimited by hyphen or underscore (e.g. GT-1187, SP-4401, ratelist_sp)
        for part in re.split(r'[-_.]', filename):
            p = part.upper()
            if p in {"GT", "SP", "BC", "KP", "BW"}:
                return p

        # 4. Prefix with separator (e.g. "GT-1187.jpg")
        code_match = re.match(r'^([A-Z]{2,4})[-_/]', filename.upper())
        if code_match:
            cand = code_match.group(1)
            if cand not in {"RATE", "BILL", "STAT", "INVO", "CHAT"}:
                return cand

        return "UNK"
