import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// The suite runs files in parallel jsdom workers; a first render after sign-in can take longer than
// findBy's one-second default on a busy machine. Three seconds keeps a real miss quick to report.
configure({ asyncUtilTimeout: 3000 });

afterEach(() => cleanup());
