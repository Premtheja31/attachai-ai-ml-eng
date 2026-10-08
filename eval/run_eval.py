"""Eval for the Part 3 extraction pipeline against eval/golden_set.json.

Runs the REAL pipeline (OpenAILLMClient — real API calls, the exact client
class the endpoint uses; never the test double) on each golden record and
scores three checks per record:

- kind:       any extracted attribute has the expected kind
- keywords:   any extracted attribute's text contains >= 1 expected keyword
              (case-insensitive)
- restricted: message-level flag — (any attribute restricted) must equal
              expected_restricted exactly

A record passes only if all three checks pass. The run FAILS (non-zero
exit) if the overall score falls below the threshold OR if any record's
restricted check fails — privacy misclassification has zero tolerance
regardless of threshold.

Usage: python -m eval.run_eval [--threshold 0.75] [--golden-path eval/golden_set.json]
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.custom_model import OpenAILLMClient
from app.llm_client import ExtractedAttribute, LLMClient


@dataclass
class RecordResult:
    message: str
    expected_kind: str
    expected_keywords: list[str]
    expected_restricted: bool
    attributes: list[ExtractedAttribute]
    error: str | None = None

    @property
    def kind_match(self) -> bool:
        return any(a["kind"] == self.expected_kind for a in self.attributes)

    @property
    def keyword_match(self) -> bool:
        return any(
            keyword.lower() in a["text"].lower()
            for a in self.attributes
            for keyword in self.expected_keywords
        )

    @property
    def restricted_match(self) -> bool:
        if self.error is not None or not self.attributes:
            return False
        extracted_restricted = any(a["restricted"] for a in self.attributes)
        return extracted_restricted == self.expected_restricted

    @property
    def passed(self) -> bool:
        return self.kind_match and self.keyword_match and self.restricted_match


class GoldenSetEvaluator:
    def __init__(self, llm: LLMClient, golden_path: Path, threshold: float) -> None:
        self.llm = llm
        self.golden_path = golden_path
        self.threshold = threshold

    def load_records(self) -> list[dict]:
        return json.loads(self.golden_path.read_text())

    def evaluate_record(self, record: dict) -> RecordResult:
        result = RecordResult(
            message=record["message"],
            expected_kind=record["expected_kind"],
            expected_keywords=record["expected_keywords"],
            expected_restricted=record["expected_restricted"],
            attributes=[],
        )
        try:
            result.attributes = self.llm.extract_attributes(record["message"])
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        return result

    def run(self) -> int:
        records = self.load_records()
        results = [self.evaluate_record(r) for r in records]

        for i, res in enumerate(results, start=1):
            self._print_record(i, len(results), res)

        passed = sum(1 for r in results if r.passed)
        score = passed / len(results)
        restricted_violations = sum(1 for r in results if not r.restricted_match)

        print(f"Overall: {passed}/{len(results)} records passed "
              f"(score {score:.2f}, threshold {self.threshold:.2f})")
        print(f"Restricted violations: {restricted_violations} (zero tolerance)")

        failed = score < self.threshold or restricted_violations > 0
        print(f"RESULT: {'FAIL' if failed else 'PASS'}")
        return 1 if failed else 0

    @staticmethod
    def _print_record(index: int, total: int, res: RecordResult) -> None:
        def mark(ok: bool) -> str:
            return "PASS" if ok else "FAIL"

        snippet = res.message if len(res.message) <= 70 else res.message[:67] + "..."
        print(f"[{index}/{total}] {snippet!r}")
        print(f"      expected: kind={res.expected_kind} restricted={res.expected_restricted} "
              f"keywords={res.expected_keywords}")
        if res.error is not None:
            print(f"      extraction FAILED: {res.error}")
        elif not res.attributes:
            print("      extracted: (no attributes)")
        else:
            for a in res.attributes:
                print(f"      extracted: kind={a['kind']} confidence={a['confidence']} "
                      f"restricted={a['restricted']} text={a['text']!r}")
        print(f"      kind={mark(res.kind_match)} keywords={mark(res.keyword_match)} "
              f"restricted={mark(res.restricted_match)} -> record "
              f"{'PASS' if res.passed else 'FAIL'}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the extraction pipeline eval.")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="minimum overall score to pass (default 0.75)")
    parser.add_argument("--golden-path", type=Path, default=Path("eval/golden_set.json"))
    args = parser.parse_args()

    evaluator = GoldenSetEvaluator(
        llm=OpenAILLMClient(), golden_path=args.golden_path, threshold=args.threshold
    )
    return evaluator.run()


if __name__ == "__main__":
    sys.exit(main())
