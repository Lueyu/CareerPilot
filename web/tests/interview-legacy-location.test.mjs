import assert from "node:assert/strict";
import test from "node:test";
import { interviewLocation } from "../src/interview/legacyLocation.ts";

test("old bookmarks retain the relevant record and open the new workbench", () => {
  const pairs = [
    ["/", "/chat"],
    ["/study/chat/52?topic=redis", "/chat/52?topic=redis"],
    ["/bank?review=1&item=28-41", "/library/notes?review=1&item=28-41"],
    ["/bank?topic=redis", "/library/questions?topic=redis"],
    ["/profile/distributed", "/competency?dimension=distributed"],
    ["/assets/project/19", "/library/projects?project=19"],
    ["/study/notes/22?tab=review", "/library/materials?tab=review&note=22"],
    ["/exam/diagnosis/report?id=9#exam-question-3", "/chat/exam/9/report?id=9#exam-question-3"],
    ["/coach/train?mode=study&id=9&session=6", "/chat/6?mode=study&id=9&session=6"],
    ["/podcasts?playlist=8", "/library/podcasts?playlist=8"],
  ];
  for (const [original, expected] of pairs) {
    const location = new URL(original, "http://careerpilot.local");
    assert.equal(interviewLocation(location), expected, original);
  }
});

test("invalid legacy exam IDs cannot become new route segments", () => {
  const path = interviewLocation({ pathname: "/exam/diagnosis/report", search: "?id=../../bank" });
  assert.equal(new URL(path, "http://careerpilot.local").pathname, "/chat/exam");
});
