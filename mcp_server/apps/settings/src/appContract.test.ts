// Pins the real @modelcontextprotocol/ext-apps App (what main.tsx passes in
// production) against the shared McpAppSurface type (what component tests
// mock). Passing the real App to describeAppContract(app: McpAppSurface) is
// the compile-time half: `bun run test` runs tsc first. The runtime half is
// in ../../_shared/appContract.ts.
import { App } from "@modelcontextprotocol/ext-apps";
import { describeAppContract } from "../../_shared/appContract";

describeAppContract(new App({ name: "contract-test", version: "0.0.0" }));
