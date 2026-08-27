"""The run summary: what a 60,000-document job has to be able to tell you.

Reading a corpus run document by document is not possible, so this is the
surface that decides whether a five-day job is going well. What it must not do
is look healthy when it is not.
"""
import json
import tempfile
import unittest
from pathlib import Path

from specter.report import load, render, summarise


def document(name="judgment.pdf", folder="Writ Petition-1-2020", score=1.0, status="PASS",
             route="digital", kind="judgment", findings=(), **extra):
    return {
        "source_name": name,
        "source_file": f"D:/corpus/IHC/2020/Judge/{folder}/{name}",
        "page_count": 3,
        "document_kind": kind,
        "router": {"selected": route},
        "diagnostics": {"status": status, "quality_score": score},
        "label_check": {"findings": list(findings)},
        **extra,
    }


class SummaryTests(unittest.TestCase):
    def test_it_counts_routes_kinds_and_statuses(self):
        report = summarise([document(), document(route="scanned", status="REVIEW", score=0.6),
                            document(kind="order")])
        self.assertEqual(report["routes"], {"digital": 2, "scanned": 1})
        self.assertEqual(report["kinds"], {"judgment": 2, "order": 1})
        self.assertEqual(report["statuses"], {"PASS": 2, "REVIEW": 1})

    def test_failures_come_from_the_manifest_because_nothing_else_records_them(self):
        # A document that failed to parse wrote no metadata file, so counting
        # only what is on disk would report a run with no failures.
        report = summarise([document()], {"failures": [{"pdf": "x.pdf", "error": "boom"}]})
        self.assertEqual(len(report["failures"]), 1)
        self.assertIn("boom", render(report))

    def test_disagreements_are_grouped_by_field_and_state(self):
        findings = [{"field": "case_number", "state": "disagrees"},
                    {"field": "judges", "state": "filled_from_sidecar"}]
        report = summarise([document(findings=findings), document(findings=findings[:1])])
        self.assertEqual(report["label_findings"]["case_number: disagrees"], 2)
        self.assertEqual(report["label_findings"]["judges: filled_from_sidecar"], 1)

    def test_the_worst_documents_are_named_by_their_case_not_their_filename(self):
        # At IHC every file is judgment.pdf, so a filename identifies nothing.
        report = summarise([document(folder="Writ Petition-9-2021", score=0.4), document()])
        self.assertEqual(report["worst"][0]["name"], "Writ Petition-9-2021/judgment.pdf")

    def test_a_statute_missing_a_section_is_called_out(self):
        # The statute says so itself; no annotation is involved.
        holed = document(name="201_provident_funds_act_1925.pdf", kind="statute",
                         metadata={"sections_complete": False})
        whole = document(name="63_carriers_act_1865.pdf", kind="statute",
                         metadata={"sections_complete": True})
        report = summarise([holed, whole])
        self.assertEqual(len(report["statutes_with_a_hole"]), 1)
        self.assertIn("provident_funds", report["statutes_with_a_hole"][0])

    def test_a_run_with_no_scores_still_renders(self):
        report = summarise([{"source_name": "x.pdf", "diagnostics": {}}])
        self.assertIsNone(report["quality"]["median"])
        self.assertIn("1 documents written", render(report))


class LoadTests(unittest.TestCase):
    def test_a_half_written_file_does_not_stop_the_report(self):
        # The point of reading the folder rather than the manifest is that a
        # run still in flight can be watched; one file caught mid-write must
        # not take the summary down with it.
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "metadata"
            meta.mkdir()
            (meta / "a.json").write_text(json.dumps(document()), encoding="utf-8")
            (meta / "b.json").write_text('{"source_name": "half', encoding="utf-8")
            self.assertEqual(len(load(Path(tmp))), 1)


if __name__ == "__main__":
    unittest.main()
