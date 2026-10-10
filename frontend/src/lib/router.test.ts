import { describe, expect, it } from "vitest";
import { href, parse } from "./router";

describe("router", () => {
  it("parses id-only routes", () => {
    expect(parse("#/")).toEqual({ name: "home" });
    expect(parse("#/player/3961-22-2-27")).toEqual({ name: "player", key: "3961-22-2-27" });
    expect(parse(href.compare("1-2-3-27", "4-5-6-27"))).toEqual({ name: "compare", a: "1-2-3-27", b: "4-5-6-27" });
    expect(parse("#/methodology")).toEqual({ name: "methodology" });
  });
  it("refuses names in place of ids", () => {
    expect(parse("#/player/kante").name).toBe("not-found");
    expect(parse("#/player/3961-22").name).toBe("not-found");
  });
});
