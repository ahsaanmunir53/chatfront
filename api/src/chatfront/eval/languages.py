"""The language evaluation (feature 121): is each Tier 1 language recognised and answered in?

    python -m chatfront.eval.languages              # mock models: detection and routing only
    python -m chatfront.eval.languages --live       # real models; the only run that counts
    python -m chatfront.eval.languages --lang ps    # one language
    ... --write                                     # store results in i18n/eval-results.json

A conversation passes when the language Chatfront settles on is the customer's (or one its
readers read, like Gulf Arabic and Modern Standard Arabic) and the reply is confidently written
in it. Voice notes count only once their recording exists.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from chatfront.ai.client import AiClient, CallRecord
from chatfront.ai.config import build_roles
from chatfront.ai.mock import MockProvider
from chatfront.ai.provider import OpenAICompatibleProvider
from chatfront.ai.types import AudioPart, ChatMessage, ChatRequest, Role, TextPart
from chatfront.channels.conversation import ShopAssistant, Turn
from chatfront.channels.language import note_language, reply_language
from chatfront.channels.models import Conversation, Direction, Message
from chatfront.channels.types import OutText
from chatfront.core.countries import get_pack
from chatfront.core.langdetect import load_detector
from chatfront.core.languages import load_registry, mutually_readable
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings, get_settings

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True, slots=True)
class Step:
    text: str | None = None
    voice: str | None = None
    transcript: str | None = None


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    lang: str
    country: str
    steps: tuple[Step, ...]

    @property
    def is_voice(self) -> bool:
        return any(step.voice for step in self.steps)


@dataclass(slots=True)
class Outcome:
    id: str
    lang: str
    passed: bool = False
    pending: bool = False
    settled_on: str | None = None
    replied_in: str | None = None
    first_try: bool = True  # the first answer was already in the right language
    problem: str | None = None


def load_cases(directory: Path = DATA_DIR) -> dict[str, list[Case]]:
    cases: dict[str, list[Case]] = {}
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases[raw["lang"]] = [
            Case(
                id=item["id"],
                lang=raw["lang"],
                country=raw["country"],
                steps=tuple(Step(**step) for step in item["turns"]),
            )
            for item in raw["conversations"]
        ]
    return cases


class CallLog:
    """Counts model calls by prompt, to tell first-try answers from repaired ones."""

    def __init__(self) -> None:
        self.prompts: Counter[str] = Counter()

    async def __call__(self, record: CallRecord) -> None:
        self.prompts[(record.prompt or "").split("@")[0]] += 1


async def _transcribe(ai: AiClient, audio: Path, tenant_id: uuid.UUID) -> str:
    data = base64.b64encode(await asyncio.to_thread(audio.read_bytes)).decode()
    request = ChatRequest(
        role=Role.AUDIO,
        messages=[
            ChatMessage(
                "system",
                "Write down exactly what the customer says in this voice note, in the language "
                "and script they would write it in. Reply with the words only.",
            ),
            ChatMessage("user", [AudioPart(f"data:audio/ogg;base64,{data}"), TextPart("")]),
        ],
        tenant_id=tenant_id,
        prompt="eval_transcribe@v1",
    )
    return ((await ai.complete(request)).content or "").strip()


def _tenant(country: str) -> Tenant:
    pack = get_pack(country)
    return Tenant(
        id=uuid.uuid4(),
        slug="eval-shop",
        name="Gulmohar Lawn House",
        template="clothing",
        country=pack.code,
        default_lang=pack.languages[0],
        profile={"city": "Lahore", "delivery": "2 to 4 days across the country"},
    )


async def run_case(case: Case, ai: AiClient, log: CallLog, voice_dir: Path) -> Outcome:
    outcome = Outcome(case.id, case.lang)
    tenant = _tenant(case.country)
    conversation = Conversation(id=uuid.uuid4())
    likely = get_pack(case.country).languages
    history: list[Message] = []
    for step in case.steps:
        text = step.text
        if step.voice:
            audio = voice_dir / step.voice
            if not audio.exists():
                outcome.pending = True
                outcome.problem = f"no recording yet: {step.voice}"
                return outcome
            text = await _transcribe(ai, audio, tenant.id)
        note_language(conversation, text, likely)
        history.append(Message(channel="webchat", kind="text", body=text, direction=Direction.IN))
    outcome.settled_on = reply_language(conversation, tenant)
    log.prompts.clear()
    current = history.pop()
    replies = await ShopAssistant(ai).respond(
        Turn(tenant, conversation, current, history, None, outcome.settled_on)
    )
    reply = replies[0]
    assert isinstance(reply, OutText)
    found = load_detector().detect(reply.text)
    outcome.replied_in = found.tag if found and found.confident else None
    outcome.first_try = log.prompts["shop_assistant"] <= 1 and not log.prompts["translate"]
    settled = mutually_readable(outcome.settled_on, case.lang)
    answered = outcome.replied_in is not None and mutually_readable(outcome.replied_in, case.lang)
    outcome.passed = settled and answered
    if not settled:
        outcome.problem = f"settled on {outcome.settled_on}"
    elif not answered:
        outcome.problem = f"replied in {outcome.replied_in or 'an unclear language'}"
    return outcome


@dataclass(slots=True)
class Summary:
    total: int = 0
    passed: int = 0
    pending: int = 0
    first_try: int = 0
    exact: int = 0  # settled on exactly the expected tag, not only a mutually readable one
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0


def summarise(outcomes: Sequence[Outcome]) -> Summary:
    summary = Summary()
    for outcome in outcomes:
        if outcome.pending:
            summary.pending += 1
            continue
        summary.total += 1
        summary.passed += outcome.passed
        summary.first_try += outcome.passed and outcome.first_try
        summary.exact += outcome.settled_on == outcome.lang
        if not outcome.passed:
            summary.failures.append(asdict(outcome))
    return summary


def build_client(settings: Settings, live: bool, log: CallLog) -> AiClient:
    roles = build_roles(settings)
    if live and not any(config.endpoints for config in roles.values()):
        raise SystemExit("A live run needs BASETEN_API_KEY or FIREWORKS_API_KEY.")
    return AiClient(
        roles,
        live=OpenAICompatibleProvider(httpx.AsyncClient(timeout=60)),
        mock=MockProvider(),
        meter=log,
        use_mock=not live,
        usd_to_pkr=settings.usd_to_pkr,
    )


async def evaluate(
    live: bool, only: Sequence[str] = (), voice_dir: Path = DATA_DIR
) -> dict[str, Summary]:
    log = CallLog()
    ai = build_client(get_settings(), live, log)
    results: dict[str, Summary] = {}
    for lang, cases in load_cases().items():
        if only and lang not in only:
            continue
        outcomes = [await run_case(case, ai, log, voice_dir) for case in cases]
        results[lang] = summarise(outcomes)
    return results


def write_results(results: dict[str, Summary], live: bool, path: Path | None = None) -> None:
    target = path or get_settings().i18n_dir / "eval-results.json"
    stored = json.loads(target.read_text(encoding="utf-8"))
    mode = "live" if live else "mock"
    when = datetime.now(UTC).date().isoformat()
    for lang, summary in results.items():
        stored["runs"].setdefault(lang, {})[mode] = {
            "pass_rate": summary.pass_rate,
            "passed": summary.passed,
            "total": summary.total,
            "first_try": summary.first_try,
            "exact_language": summary.exact,
            "voice_pending": summary.pending,
            "date": when,
        }
    target.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _report(results: dict[str, Summary], pass_mark: Decimal) -> str:
    lines = [f"{'language':<10} {'pass':>6} {'first try':>10} {'exact':>6} {'voice pending':>14}"]
    for lang, s in results.items():
        flag = "" if s.pass_rate >= pass_mark else "  below pass mark"
        lines.append(
            f"{lang:<10} {s.passed:>3}/{s.total:<2} {s.first_try:>7}/{s.total:<2}"
            f" {s.exact:>3}/{s.total:<2} {s.pending:>14}{flag}"
        )
        lines += [f"    {f['id']}: {f['problem']}" for f in s.failures]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="use the real models")
    parser.add_argument("--lang", action="append", default=[], help="only this language")
    parser.add_argument("--write", action="store_true", help="save to i18n/eval-results.json")
    args = parser.parse_args(argv)
    results = asyncio.run(evaluate(args.live, args.lang))
    print(_report(results, Decimal(str(load_registry().pass_mark))))
    if args.write:
        write_results(results, args.live)


if __name__ == "__main__":
    main()
