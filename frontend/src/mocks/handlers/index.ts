import { conversationHandlers } from "./conversations";
import { chatHandlers } from "./chat";
import { documentHandlers } from "./documents";
import { exportHandlers } from "./exports";
import { matterHandlers } from "./matters";

export const handlers = [
  ...conversationHandlers,
  ...chatHandlers,
  ...documentHandlers,
  ...exportHandlers,
  ...matterHandlers,
];
