"""``exocortex_engine.audit``: Finding shape, rule registration and exception isolation."""
from __future__ import annotations

import unittest

import _workspace  # noqa: F401  (puts src/ on sys.path)

from exocortex_engine import audit
from exocortex_engine.audit import Finding, Registry, Subject, Suggest


class _Ctx:
    main = "refs/heads/main"


def finding(rule: str = "stale-thing", **kw) -> Finding:
    args = dict(rule=rule, severity="warn", subject=Subject("path", "contexts/x.md"), message="one line")
    args.update(kw)
    return Finding(**args)


class FindingTests(unittest.TestCase):
    def test_to_dict_matches_contract(self):
        f = finding(suggest=Suggest("git branch x origin/x", mutates=True))
        self.assertEqual(f.to_dict(), {
            "rule": "stale-thing", "severity": "warn", "subject": {"kind": "path", "id": "contexts/x.md"},
            "message": "one line", "suggest": {"command": "git branch x origin/x", "mutates": True},
        })
        self.assertIsNone(finding().to_dict()["suggest"])

    def test_validation(self):
        with self.assertRaises(ValueError):
            finding(rule="Not_Kebab")
        with self.assertRaises(ValueError):
            finding(severity="fatal")
        with self.assertRaises(ValueError):
            Subject("file", "x")
        with self.assertRaises(ValueError):
            finding(message="two\nlines")
        with self.assertRaises(ValueError):
            Suggest("cmd", mutates="yes")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Suggest("  ", mutates=False)


class RegistryTests(unittest.TestCase):
    def test_default_registry_is_empty(self):
        self.assertEqual(audit.rule_count(), 0)
        self.assertEqual(audit.run_all(_Ctx()), [])

    def test_register_and_run_in_order(self):
        reg = Registry()
        reg.register("second-rule", lambda ctx: [finding("second-rule")])
        reg.register("first-rule", lambda ctx: iter([finding("first-rule"), finding("first-rule", severity="info")]))
        reg.register("quiet-rule", lambda ctx: [])
        self.assertEqual(len(reg), 3)
        self.assertEqual(reg.names(), ["second-rule", "first-rule", "quiet-rule"])
        got = reg.run_all(_Ctx())
        self.assertEqual([(f.rule, f.severity) for f in got],
                         [("second-rule", "warn"), ("first-rule", "warn"), ("first-rule", "info")])

    def test_register_rejects_bad_input(self):
        reg = Registry()
        reg.register("a-rule", lambda ctx: [])
        with self.assertRaises(ValueError):
            reg.register("a-rule", lambda ctx: [])
        with self.assertRaises(ValueError):
            reg.register("Bad Name", lambda ctx: [])
        with self.assertRaises(ValueError):
            reg.register("not-callable", None)  # type: ignore[arg-type]

    def test_exception_is_isolated(self):
        def boom(ctx):
            raise RuntimeError("disk on fire\nsecond line")

        reg = Registry()
        reg.register("boom-rule", boom)
        reg.register("wrong-type", lambda ctx: ["not a finding"])
        reg.register("wrong-name", lambda ctx: [finding("someone-else")])
        reg.register("fine-rule", lambda ctx: [finding("fine-rule")])
        got = reg.run_all(_Ctx())
        self.assertEqual([f.rule for f in got], ["boom-rule", "wrong-type", "wrong-name", "fine-rule"])
        self.assertEqual([f.severity for f in got[:3]], ["error"] * 3)
        self.assertEqual(got[0].message, "rule failed: RuntimeError: disk on fire")
        self.assertEqual(got[0].subject, Subject("ref", "main"))
        self.assertIsNone(got[0].suggest)
        self.assertEqual(got[3].severity, "warn")


if __name__ == "__main__":
    unittest.main()
