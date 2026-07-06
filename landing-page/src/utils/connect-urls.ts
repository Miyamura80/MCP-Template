/**
 * The two connect-widget endpoint URLs, in a leaf module so the widget's
 * client-side script can import them without pulling the whole landing config
 * into the browser bundle.
 */
import { connect } from "../config/landing";

export const connectUrls: Record<"demo" | "live", string> = {
  demo: connect.demoUrl,
  live: connect.mcpUrl,
};
