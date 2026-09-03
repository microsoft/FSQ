# Automation Basics

- Use the platform tools exposed by the active harness for automation interactions. Do not invent actions outside the provided platform tools.
- Prefer locators from current platform tool responses, observations, and action evidence. These have higher priority than knowledge because knowledge can be stale; use knowledge only as flow reference and candidate target guidance.
- Use platform tool assertion or verification capabilities for verify, assert, confirm, check, ensure, and validate requirements. Do not decide pass or fail only from agent narrative, screenshots, stale artifacts, or ad-hoc observation.
- Prefer deterministic assertions when a stable locator, unique text, or simple state check is available. If the required verification would depend on a brittle or complex locator, such as a long XPath through repeated generic controls, prefer `assert_with_ai` while the UI is still on the intended state.
