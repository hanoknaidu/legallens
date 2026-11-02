import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "./layouts/AppShell";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { ChatPage } from "@/pages/ChatPage";
import { NewChatPage } from "@/pages/NewChatPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { DocumentPage } from "@/pages/DocumentPage";
import { LoginPage } from "@/pages/LoginPage";
import { SettingsPage } from "@/pages/SettingsPage";

export const router = createBrowserRouter([
  // Outside the guard, necessarily: the page you are sent to when you have no session
  // cannot itself require one.
  { path: "/login", element: <LoginPage /> },
  {
    // `RequireAuth` renders the shell only once a session is confirmed, so no
    // authenticated screen ever mounts and fires its queries anonymously — which would
    // show a logged-out user a frame of empty lists before redirecting them.
    element: <RequireAuth />,
    children: [
      {
        path: "/",
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/chat/new" replace /> },
          { path: "chat/new", element: <NewChatPage /> },
          { path: "chat/:conversationId", element: <ChatPage /> },
          { path: "documents", element: <DocumentsPage /> },
          { path: "documents/:documentId", element: <DocumentPage /> },
          { path: "settings", element: <SettingsPage /> },
          { path: "*", element: <Navigate to="/chat/new" replace /> },
        ],
      },
    ],
  },
]);
