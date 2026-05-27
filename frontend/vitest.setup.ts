// Vitest global setup. Extends `expect` with the @testing-library/jest-dom
// matchers (`toBeInTheDocument`, `toHaveTextContent`, etc.) and cleans
// up the DOM between tests.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
