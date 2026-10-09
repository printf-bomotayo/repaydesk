import { actorLabel, day, naira, stamp } from "./format";

test("naira shows whole naira plainly and kobo only when there are some", () => {
  expect(naira(60_500_00)).toBe("₦60,500");
  expect(naira(60_500_50)).toBe("₦60,500.50");
  expect(naira(5)).toBe("₦0.05");
  expect(naira(0)).toBe("₦0");
  expect(naira(-2_500_000_00)).toBe("−₦2,500,000");
});

test("naira stays exact for large amounts", () => {
  expect(naira(123_456_789_012_34)).toBe("₦123,456,789,012.34");
});

test("a value date is a calendar day, whatever the viewer's time zone", () => {
  expect(day("2026-10-07")).toBe("7 Oct");
});

test("a moment is shown in the bank's time", () => {
  // 23:30 UTC on 8 October is 00:30 on 9 October in Lagos.
  expect(stamp("2026-10-08T23:30:00Z")).toBe("9 Oct, 00:30:00");
});

test("actors read as words", () => {
  expect(actorLabel("rule:virtual_account")).toBe("Rule: virtual account");
  expect(actorLabel("agent:investigator")).toBe("Agent: investigator");
  expect(actorLabel("amaka")).toBe("amaka");
});
