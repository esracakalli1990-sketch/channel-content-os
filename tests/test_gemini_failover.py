"""Gemini overload failover — the free tier answers 503 for one model while
others are fine, three nights running."""
import unittest
from channel_ops.providers import gemini_provider as gp


class _Provider(gp.GeminiProvider):
    """Real logic, stubbed network."""

    def __init__(self, healthy, models):
        self._api_key = "k"
        self._model = "gemini-flash-latest"
        self._healthy = healthy
        self._models = models
        self.calls = []

    def available_models(self):
        return list(self._models)

    def _generate_once(self, prompt, system_prompt):
        self.calls.append(self._model)
        if self._model in self._healthy:
            return f"ok:{self._model}"
        raise gp._Transient("HTTP 503")


class FailoverTests(unittest.TestCase):
    def setUp(self):
        self._sleep = gp.time.sleep
        gp.time.sleep = lambda seconds: None
        self.addCleanup(setattr, gp.time, "sleep", self._sleep)

    def test_an_overloaded_model_is_abandoned_for_another(self):
        p = _Provider(healthy={"gemini-2.5-flash"},
                      models=["gemini-flash-latest", "gemini-2.5-flash"])
        self.assertEqual(p.generate("hi"), "ok:gemini-2.5-flash")
        self.assertIn("gemini-flash-latest", p.calls)
        self.assertIn("gemini-2.5-flash", p.calls)

    def test_the_same_model_is_never_retried_after_switching(self):
        p = _Provider(healthy={"gemini-2.5-flash-lite"},
                      models=["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"])
        p.generate("hi")
        self.assertEqual(len(set(p.calls)), len([m for m in p.calls[::gp._MAX_ATTEMPTS]]))

    def test_a_healthy_model_needs_no_switch(self):
        p = _Provider(healthy={"gemini-flash-latest"}, models=["gemini-flash-latest", "x-flash"])
        self.assertEqual(p.generate("hi"), "ok:gemini-flash-latest")
        self.assertEqual(p.calls, ["gemini-flash-latest"])

    def test_everything_down_reports_every_model_tried(self):
        p = _Provider(healthy=set(),
                      models=["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"])
        with self.assertRaises(RuntimeError) as caught:
            p.generate("hi")
        message = str(caught.exception)
        self.assertIn("gemini-flash-latest", message)
        self.assertIn("gemini-2.5-flash", message)

    def test_it_gives_up_rather_than_looping_forever(self):
        p = _Provider(healthy=set(), models=[f"m{i}-flash" for i in range(20)])
        with self.assertRaises(RuntimeError):
            p.generate("hi")
        self.assertLessEqual(len(set(p.calls)), gp._MAX_MODELS)

    def test_a_single_model_account_still_fails_cleanly(self):
        p = _Provider(healthy=set(), models=["gemini-flash-latest"])
        with self.assertRaises(RuntimeError):
            p.generate("hi")


if __name__ == "__main__":
    unittest.main()


class TimeoutClassificationTests(unittest.TestCase):
    """A read timeout used to escape every handler.

    urllib wraps failures raised while *opening* a connection in URLError, but
    a timeout on the socket read surfaces as a bare TimeoutError from
    getresponse(). It therefore skipped the retries and the model failover
    entirely and propagated out of the pipeline: on 25 August a slow answer to
    a caption request left a finished video sitting in the queue past its slot.
    """

    def _call(self, error):
        provider = gp.GeminiProvider.__new__(gp.GeminiProvider)
        provider._api_key = "k"
        provider._model = "gemini-flash-latest"

        def explode(*args, **kwargs):
            raise error

        original = gp.urlopen
        gp.urlopen = explode
        self.addCleanup(setattr, gp, "urlopen", original)
        return provider

    def test_a_read_timeout_is_transient(self):
        provider = self._call(TimeoutError("The read operation timed out"))
        with self.assertRaises(gp._Transient):
            provider._generate_once("hi", None)

    def test_other_socket_errors_are_transient_too(self):
        provider = self._call(ConnectionResetError("peer hung up"))
        with self.assertRaises(gp._Transient):
            provider._generate_once("hi", None)
