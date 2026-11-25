"""Run locally with python .github/workflows/tests/test_pr_content_review.py."""

import io
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

workflow = Path(__file__).resolve().parents[1] / "pr-content-review.yml"
workflow_text = workflow.read_text(encoding="utf-8")
script = textwrap.dedent(workflow_text.split("<<'PY'\n", 1)[1].rsplit("\n          PY", 1)[0])
pending = "content review: pending"
flagged = "content review: possible threat"


class ContentReviewTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.event = Path(temp) / "event.json"
        self.enterContext(patch.dict(os.environ, {
            "GITHUB_EVENT_PATH": str(self.event),
            "GITHUB_API_URL": "https://api.invalid",
            "GITHUB_REPOSITORY": "base/project",
            "GH_TOKEN": "fixture-only",
        }))
        self.enterContext(patch("urllib.request.urlopen", side_effect=self.api))
        self.labels = {"bug"}
        self.definitions = set()
        self.writes = []
        self.state = "open"

    def api(self, request, timeout):
        path = unquote(urlsplit(request.full_url).path).removeprefix("/repos/base/project")
        method = request.method
        payload = json.loads(request.data) if request.data else None
        result = {}
        status = 200
        if (method, path) == ("GET", "/pulls/17"):
            result = {
                "state": self.state, "title": self.title, "body": self.body,
                "labels": [{"name": name} for name in self.labels],
            }
        elif method == "GET" and path.startswith("/labels/"):
            if path.removeprefix("/labels/") not in self.definitions:
                raise HTTPError(request.full_url, 404, "Missing", {}, None)
        elif (method, path) == ("POST", "/labels"):
            self.definitions.add(payload["name"])
        elif (method, path) == ("POST", "/issues/17/labels"):
            self.assertLessEqual(set(payload["labels"]), self.definitions)
            self.labels.update(payload["labels"])
        elif method == "DELETE" and path.startswith("/issues/17/labels/"):
            self.labels.remove(path.removeprefix("/issues/17/labels/"))
            status = 204
        else:
            self.fail(f"Unexpected API call: {method} {path}")
        if method != "GET":
            self.writes.append((method, path, payload))
        response = io.BytesIO(json.dumps(result).encode())
        response.status = status
        return response

    def run_event(self, action, title, body, event_body=None):
        event = {
            "action": action, "repository": {"private": False},
            "pull_request": {
                "number": 17, "title": title, "body": event_body,
                "head": {"repo": {"id": 2, "fork": True}}, "base": {"repo": {"id": 1}},
            },
        }
        if action == "edited":
            event["changes"] = {"body": {"from": "previous revision"}}
        self.event.write_text(json.dumps(event), encoding="utf-8")
        self.title, self.body = title, body
        exec(compile(script, str(workflow), "exec"), {})

    def test_criticism_repeated_edits_and_label_transitions(self):
        self.run_event("opened", "This implementation is broken", None)
        self.assertEqual(self.labels, {"bug", pending})
        writes = len(self.writes)
        self.run_event("edited", "Please replace this approach", "I will kill the background process.")
        self.assertEqual(len(self.writes), writes)
        self.run_event("edited", "Please replace this approach", "If you merge this, I’ll hurt you.")
        self.assertEqual(self.labels, {"bug", flagged})
        self.run_event("edited", "Please replace this approach", "The error handling still fails.",
                       event_body="I will hurt you.")
        self.assertEqual(self.labels, {"bug", pending})
        self.assertEqual(sum(path == "/labels" for _, path, _ in self.writes), 2)

    def test_reopened_threat_in_title(self):
        self.run_event("reopened", "I will hurt your family", "")
        self.assertEqual(self.labels, {"bug", flagged})

    def test_chinese_threat_and_ordinary_criticism(self):
        self.run_event("opened", "这个改动很糟糕", "我要关闭这个功能。")
        self.assertEqual(self.labels, {"bug", pending})
        self.run_event("edited", "这个改动很糟糕", "不要合并，我会打死你。")
        self.assertEqual(self.labels, {"bug", flagged})

    def test_closed_pull_request_is_skipped(self):
        self.state = "closed"
        with self.assertRaises(SystemExit) as result:
            self.run_event("edited", "Title", "Body")
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
