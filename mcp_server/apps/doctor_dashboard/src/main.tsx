import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App as McpApp } from "@modelcontextprotocol/ext-apps";
import { Dashboard } from "./Dashboard";

const mcpApp = new McpApp();
mcpApp.connect();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Dashboard mcpApp={mcpApp} />
  </StrictMode>
);
