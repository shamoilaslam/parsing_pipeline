"""Retrieval, fusion, and the evaluation set the corpus labels for us."""

import unittest

from rag.embed import BM25, HashingEmbedder, tokenize
from rag.evaluate import CITATION, Query, build_queries, score, statute_key
from rag.retrieve import HybridIndex


def chunk(chunk_id, text, citation=None, kind="statute", document_id="d"):
    return {"id": chunk_id, "document_id": document_id, "text": text, "prefix": "",
            "citation": citation, "document_kind": kind, "source": "test"}


class TokenTests(unittest.TestCase):
    def test_a_section_number_survives_tokenisation(self):
        # "302 PPC" and "489-F" are what must match exactly; splitting the
        # digits from the letters would lose the citation.
        self.assertIn("302", tokenize("section 302 PPC"))
        self.assertIn("ppc", tokenize("section 302 PPC"))
        self.assertIn("489", tokenize("under section 489-F"))


class BM25Tests(unittest.TestCase):
    def test_the_cited_section_outranks_its_neighbours(self):
        corpus = [chunk("a", "Punishment of qatl-i-amd. Whoever commits qatl-e-amd shall be punished."),
                  chunk("b", "Punishment for attempting to commit offences."),
                  chunk("c", "Cheating and dishonestly inducing delivery of property.")]
        # One score per chunk, zero where the query shares no term -- returned
        # as an array so accumulation is a scatter-add rather than a Python
        # loop over postings.
        scores = BM25().fit(c["text"] for c in corpus).score("qatl-i-amd punishment")
        self.assertEqual(int(scores.argmax()), 0)
        self.assertEqual(float(scores[2]), 0.0)


class DeterminismTests(unittest.TestCase):
    def test_the_stand_in_embedder_is_stable_across_processes(self):
        """Python randomises str.hash per process.

        Bucketing on it put the same token in a different slot on every run, so
        an index saved by one process and queried by another returned nothing
        sensible -- silently, because nothing errors.
        """
        import subprocess
        import sys

        code = ("import sys;sys.path.insert(0,'.');"
                "from rag.embed import HashingEmbedder;"
                "v=HashingEmbedder().encode(['section 302 PPC qatl']).dense[0];"
                "print([i for i,x in enumerate(v) if x])")
        runs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True).stdout.strip() for _ in range(3)}
        self.assertEqual(len(runs), 1, runs)


class FusionTests(unittest.TestCase):
    def index(self):
        corpus = [chunk("s302", "Punishment of qatl-i-amd. Whoever commits qatl-e-amd shall be punished with death as qisas.", "302 PPC"),
                  chunk("s420", "Cheating and dishonestly inducing delivery of property.", "420 PPC"),
                  chunk("s511", "Punishment for attempting to commit offences punishable with imprisonment.", "511 PPC")]
        return HybridIndex.build(corpus, HashingEmbedder()), corpus

    def test_every_hit_says_which_leg_found_it(self):
        index, _ = self.index()
        hits = index.search("qatl-i-amd qisas", HashingEmbedder(), limit=3)
        self.assertEqual(hits[0].chunk_id, "s302")
        # Fusion is by rank, so a hit records where each leg placed it -- which
        # is what makes a bad leg visible rather than averaged away.
        self.assertTrue(set(hits[0].legs) <= {"bm25", "dense", "sparse"})
        self.assertTrue(hits[0].legs)

    def test_a_single_leg_can_be_measured_on_its_own(self):
        """Fusion weights legs equally, so a weak leg drags a strong one down.

        Fusing BM25 with the deliberately dumb stand-in embedder scored 0.104
        recall@5 on the real evaluation set where BM25 alone scored 0.974. Only
        a per-leg measurement shows that, so the search must be able to run one
        leg at a time.
        """
        index, _ = self.index()
        embedder = HashingEmbedder()
        only_bm25 = index.search("qatl-i-amd qisas", embedder, limit=3, use=("bm25",))
        self.assertEqual(set(only_bm25[0].legs), {"bm25"})
        only_dense = index.search("qatl-i-amd qisas", embedder, limit=3, use=("dense",))
        self.assertEqual(set(only_dense[0].legs), {"dense"})

    def test_bm25_alone_works_without_an_embedder(self):
        # The dense index takes days to build; BM25 must stand on its own.
        index = HybridIndex([chunk("s302", "Punishment of qatl-i-amd.", "302 PPC"),
                             chunk("s420", "Cheating and dishonestly inducing delivery.", "420 PPC")])
        self.assertEqual(index.search("qatl-i-amd", limit=1)[0].chunk_id, "s302")


class RankingDeterminismTests(unittest.TestCase):
    """Equal scores must rank the same way every run.

    `argpartition` selects the top k without sorting, and orders equal scores
    arbitrarily. Left like that, 12 queries in 3,000 ranked differently from one
    run to the next -- and a retrieval figure that moves on its own is not a
    figure anyone can check.
    """

    def test_tied_chunks_rank_by_index_every_time(self):
        # Three chunks with the same words score identically on any query.
        tied = [chunk(f"s{n}", "Punishment of qatl-i-amd. Whoever commits qatl-e-amd.", f"{n} PPC")
                for n in (302, 303, 304)]
        index = HybridIndex(tied)
        orders = {tuple(hit.chunk_id for hit in index.search("qatl-i-amd", limit=3, use=("bm25",)))
                  for _ in range(5)}
        self.assertEqual(len(orders), 1, orders)

    def test_a_repeated_query_term_does_not_change_the_ranking(self):
        # Terms are now taken once each with their count as a factor, which is
        # what made a paragraph-length query affordable; it must not move
        # anything.
        corpus = [chunk("a", "Punishment of qatl-i-amd. Whoever commits qatl-e-amd shall be punished."),
                  chunk("b", "Cheating and dishonestly inducing delivery of property."),
                  chunk("c", "Punishment for attempting to commit offences.")]
        index = HybridIndex(corpus)
        once = [hit.chunk_id for hit in index.search("punishment qatl", limit=3, use=("bm25",))]
        twice = [hit.chunk_id for hit in index.search("punishment punishment qatl qatl",
                                                      limit=3, use=("bm25",))]
        self.assertEqual(once, twice)


class ExactCitationTests(unittest.TestCase):
    """A citation is an identifier, not a bag of words.

    Asked for "section 302 PPC", lexical search put the right section first only
    35% of the time -- it competes with every other section saying "punishment"
    and every cross-reference mentioning 302 -- while the query named the answer
    outright. Matching the identifier takes that to 100%.
    """

    def index(self):
        return HybridIndex([
            chunk("s302", "302. Punishment of qatl-i-amd. Whoever commits qatl-e-amd.", "302 PPC"),
            chunk("s303", "303. Qatl committed under ikrah-i-tam.", "303 PPC"),
            chunk("s497", "497. When bail may be taken in a non-bailable offence.", "497 CRPC")])

    def test_a_query_naming_a_section_returns_that_section_first(self):
        hits = self.index().search("what does section 302 PPC say", limit=3, use=("exact",))
        self.assertEqual(hits[0].chunk_id, "s302")

    def test_the_leg_is_silent_when_the_query_names_nothing(self):
        # This is what proves the in-context queries carry no citation: a leg
        # that needs one scores zero on them.
        self.assertEqual(self.index().search("the deceased was shot", limit=3, use=("exact",)), [])

    def test_a_citation_for_a_section_we_do_not_hold_matches_nothing(self):
        self.assertEqual(self.index().search("section 999 PPC", limit=3, use=("exact",)), [])

    def test_it_is_a_leg_and_not_a_short_circuit(self):
        # A query may name a section *and* describe a problem; the description
        # still has to be searched.
        hits = self.index().search("section 302 PPC bail non-bailable offence", limit=3)
        self.assertEqual(hits[0].chunk_id, "s302")
        self.assertIn("s497", {hit.chunk_id for hit in hits})


class EvaluationSetTests(unittest.TestCase):
    def test_a_judgment_citing_a_section_becomes_a_labelled_query(self):
        statutes = [chunk("s302", "Punishment of qatl-i-amd. Whoever commits qatl-e-amd.", "302 PPC")]
        judgments = [chunk(
            "j1",
            "The appellant was convicted under section 302 PPC for causing the death of the "
            "deceased by firing at him with a pistol, and the trial court sentenced him to "
            "imprisonment for life having regard to the mitigating circumstances of the case.",
            kind="judgment", document_id="2011LHC1")]
        queries = build_queries(judgments, statutes)
        kinds = {q.kind for q in queries}
        self.assertEqual(kinds, {"lookup", "in_context"})
        self.assertTrue(all(q.gold == {"s302"} for q in queries))

    def test_the_citation_is_removed_from_the_in_context_query(self):
        # Left in, BM25 answers it from the number alone and the evaluation
        # says nothing about the dense leg.
        statutes = [chunk("s302", "Punishment of qatl-i-amd.", "302 PPC")]
        judgments = [chunk("j1", "The appellant was convicted under section 302 PPC for causing "
                                 "the death of the deceased by firing at him with a pistol during "
                                 "an altercation over a longstanding land dispute between them. "
                                 "The learned trial court relied upon the ocular account furnished "
                                 "by two eye-witnesses whose presence at the spot stood established.",
                           kind="judgment", document_id="d")]
        in_context = [q for q in build_queries(judgments, statutes) if q.kind == "in_context"]
        self.assertEqual(len(in_context), 1)
        self.assertNotIn("302", in_context[0].text)

    def test_a_citation_we_do_not_hold_makes_no_query(self):
        judgments = [chunk("j1", "convicted under section 999 XYZ of something", kind="judgment")]
        self.assertEqual(build_queries(judgments, []), [])

    def test_statute_key_matches_a_mention_to_a_section(self):
        self.assertEqual(statute_key("302 PPC"), "302|PPC")
        self.assertIsNone(statute_key(None))
        self.assertIsNone(statute_key("section 3 of THE STAMP ACT, 1899"))


class ScoreTests(unittest.TestCase):
    class FakeHit:
        def __init__(self, chunk_id): self.chunk_id = chunk_id

    def test_metrics_are_reported_per_query_kind(self):
        queries = [Query("q1", "lookup", "section 302 PPC", {"s302"}, "d"),
                   Query("q2", "in_context", "the deceased was shot", {"s302"}, "d")]
        def search(text):
            return [self.FakeHit("s302")] if "302" in text else [self.FakeHit("s420"),
                                                                 self.FakeHit("s302")]
        report = score(queries, search)
        self.assertEqual(report["lookup"]["recall@1"], 1.0)
        self.assertEqual(report["in_context"]["recall@1"], 0.0)
        self.assertEqual(report["in_context"]["recall@5"], 1.0)
        self.assertEqual(report["in_context"]["mrr"], 0.5)


if __name__ == "__main__":
    unittest.main()
