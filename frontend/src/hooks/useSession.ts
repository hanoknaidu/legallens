import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { SessionUser } from "@/types/domain";

export const SESSION_KEY = ["auth", "session"] as const;

/**
 * Who is signed in.
 *
 * `GET /auth/session` answers 200 with `{ user: null }` when nobody is, so "signed
 * out" is data rather than an error — which is what lets the login page and the route
 * guard share one query instead of racing each other.
 *
 * `retry: false`: if this fails the app cannot establish identity, and retrying three
 * times only makes the user wait longer to be told so.
 */
export function useSession() {
  const query = useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => api.auth.session(),
    retry: false,
    staleTime: 60_000,
  });
  return {
    user: query.data?.user ?? null,
    /**
     * Whether an unrecognised email may sign in and get an account. From the server,
     * not from a build-time constant: it is deployment configuration, and a login
     * page that guessed would tell half of all deployments the wrong thing.
     */
    openSignin: query.data?.openSignin ?? false,
    isLoading: query.isPending,
    isError: query.isError,
    refetch: query.refetch,
  };
}

export function useSignIn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) => api.auth.signIn(body),
    onSuccess: (data: { user: SessionUser; openSignin: boolean }) => {
      // Seed rather than invalidate: the response already carries the user, and a
      // refetch here would make the redirect wait on a second round trip.
      queryClient.setQueryData(SESSION_KEY, { user: data.user, openSignin: data.openSignin });
    },
  });
}

export function useSignOut() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.auth.signOut(),
    onSuccess: () => {
      // Order matters, and `queryClient.clear()` cannot be used here.
      //
      // Clear destroys every Query *object*, including the one the mounted
      // `useSession` observer in `RequireAuth` is subscribed to. A `setQueryData`
      // afterwards creates a fresh object that nobody is observing, so the guard
      // never learns the session ended and the user stays in the app looking at the
      // previous session's screen. Caught by e2e TC-10.5.
      //
      // So: publish the signed-out session first, on the query that is actually being
      // watched — that is what triggers the redirect — then drop everything else.
      queryClient.setQueryData(SESSION_KEY, {
        user: null,
        // Preserved: it describes the deployment, not the person who just left, and
        // dropping it would make the login page claim accounts are operator-created
        // until the next fetch lands.
        openSignin:
          queryClient.getQueryData<{ openSignin?: boolean }>(SESSION_KEY)?.openSignin ?? false,
      });

      // Every other cached query belongs to the user who just left: their
      // conversations, their documents, their audit rows. Removing them is not
      // tidiness — without it the next person to sign in on this browser sees the
      // previous one's data until each query happens to refetch.
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== SESSION_KEY[0],
      });
    },
  });
}
