import { actorLabel, ago, bankToday, day, fullDay, naira, phone, stamp } from "./format";

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

test("dates and phones for the borrower and loan pages", () => {
  expect(fullDay("2026-08-05")).toBe("5 Aug 2026");
  // 23:30 UTC on 8 Oct is already 9 Oct in Lagos, as Fineract dates arrears.
  expect(bankToday(new Date("2026-10-08T23:30:00Z"))).toBe("2026-10-09");
  const now = new Date("2026-10-09T10:00:00Z");
  expect(ago("2026-10-09T09:59:50Z", now)).toBe("just now");
  expect(ago("2026-10-09T09:56:00Z", now)).toBe("4 minutes ago");
  expect(ago("2026-10-09T07:00:00Z", now)).toBe("3 hours ago");
  expect(phone("07005566957")).toBe("0700 556 6957");
  expect(phone(null)).toBe("–");
});
