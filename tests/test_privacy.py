import _isolate  # noqa: F401  (must come first)
import json
import tempfile
import unittest
from pathlib import Path

from curriculum import privacy, server


class PersonalReferencesTests(unittest.TestCase):
    def test_catches_talk_about_the_person(self):
        for text in [
            "my gambling problem",
            "I'm trying to quit",
            "helps me cope",
            "the user is struggling",
            "I struggle with impulse control",
            "I have a hard time stopping",
            "i need to understand this",
        ]:
            self.assertTrue(privacy.personal_references(text), text)

    def test_leaves_subject_matter_alone(self):
        for text in [
            "how the gambler's fallacy distorts judgment of independent events",
            "the causes of World War I",
            "World War I and II compared",
            "Type I and Type II errors in testing",
            "Phase I clinical trials",
            "judging a decision by its process, not its outcome",
            "loss aversion",
        ]:
            self.assertEqual(privacy.personal_references(text), [], text)


class ToolReviewTests(unittest.TestCase):
    """The review runs on tags and concept names too, not only descriptions."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        token = server._tenant_db.set(Path(self.tmp.name) / "t.db")
        self.addCleanup(server._tenant_db.reset, token)

    def names(self):
        return [t["name"] for t in json.loads(server.get_curriculum())]

    def test_personal_description_refused_until_reviewed(self):
        out = server.log_topic(name="Loss aversion", type="concept", description="explains my habit")
        self.assertTrue(out.startswith("NOT SAVED"))
        self.assertIn("description", out)
        ok = server.log_topic(name="Loss aversion", type="concept", description="explains my habit", reviewed=True)
        self.assertIn("Loss aversion", ok)

    def test_personal_tags_refused(self):
        out = server.log_topic(name="Loss aversion", type="concept", description="losses weigh more than gains", tags=["economics", "my gambling problem"])
        self.assertTrue(out.startswith("NOT SAVED"))
        self.assertIn("tags", out)
        self.assertTrue(server.get_curriculum().startswith("NOT_SET_UP"))  # nothing was saved

    def test_personal_concept_name_refused_but_work_titles_are_not(self):
        out = server.log_topic(name="How I beat my addiction", type="concept", description="x")
        self.assertTrue(out.startswith("NOT SAVED"))
        self.assertIn("name", out)
        # a book's title is someone else's wording
        ok = server.log_topic(name="Me Talk Pretty One Day", type="book", author="David Sedaris", description="essays on language learning")
        self.assertNotIn("NOT SAVED", ok)
        self.assertEqual(self.names(), ["Me Talk Pretty One Day"])

    def test_update_topic_reviews_tags_and_new_concept_name(self):
        server.log_topic(name="Loss aversion", type="concept", description="losses weigh more than gains")
        self.assertTrue(server.update_topic(name="Loss aversion", tags=["my problem"]).startswith("NOT SAVED"))
        self.assertTrue(server.update_topic(name="Loss aversion", new_name="what I struggle with").startswith("NOT SAVED"))
        self.assertEqual(self.names(), ["Loss aversion"])
        self.assertNotIn("NOT SAVED", server.update_topic(name="Loss aversion", tags=["economics", "psychology"]))

    def test_update_keeps_existing_work_title_unchecked(self):
        server.log_topic(name="Some Book", type="book", description="about habits")
        self.assertNotIn("NOT SAVED", server.update_topic(name="Some Book", new_name="My Struggle"))

    def test_personal_reason_still_refused(self):
        server.log_topic(name="Loss aversion", type="concept", description="losses weigh more than gains")
        out = server.save_recommendation(title="Thinking, Fast and Slow", reason="helps me with my gambling", topic_name="Loss aversion")
        self.assertTrue(out.startswith("NOT SAVED"))
        self.assertNotIn("NOT SAVED", server.save_recommendation(title="Thinking, Fast and Slow", reason="develops loss aversion through prospect theory", topic_name="Loss aversion"))


class PromptWordingTests(unittest.TestCase):
    """The wording that steers the client model is part of the behavior; keep it from drifting away."""

    def doc(self, fn):
        return " ".join((fn.__doc__ or "").split())

    def test_log_topic_prompt(self):
        d = self.doc(server.log_topic)
        for phrase in ["Describe the subject, not the person", "tags", "private in themselves",
                       "ask first", "not to save", "delete_topic"]:
            self.assertIn(phrase, d)

    def test_save_recommendation_prompt(self):
        d = self.doc(server.save_recommendation)
        for phrase in ["anything about their health", "ask before saving",
                       "not to save", "delete_recommendation"]:
            self.assertIn(phrase, d)

    def test_delete_tools_mention_forgetting(self):
        self.assertIn("forget", self.doc(server.delete_topic))
        self.assertIn("forget", self.doc(server.delete_recommendation))


if __name__ == "__main__":
    unittest.main()
