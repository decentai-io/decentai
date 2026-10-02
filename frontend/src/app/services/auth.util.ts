/**
 * Where to send someone who needs to (re)authenticate.
 *
 * The root component resolves the session on boot and shows the sign-in
 * screen when there isn't one, so "go authenticate" is simply "reload the
 * app", in every environment.
 */
export function loginUrl(): string {
  return '/';
}
