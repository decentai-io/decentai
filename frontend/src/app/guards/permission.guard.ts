import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from '../services/auth.service';
import { firstVisiblePage } from '../website/config/config';

/**
 * Blocks a route when the session's policy does not grant its
 * `data.requiredAction`, sending the user to the first page they may
 * actually open instead.
 *
 * Every page declares an action — there is no "public by omission". A route
 * that forgets `requiredAction` is DENIED rather than opened, so the only way
 * to make a page visible is to grant its action from the admin page (to a
 * group, or to everyone via the built-in Everyone group).
 *
 * The redirect is resolved from the same route config the sidebar renders,
 * never a fixed page: a hardcoded fallback that the user also lacks would
 * bounce them from one denial to the next. When their policy opens nothing
 * at all, they get told so rather than sent in circles.
 *
 * The sidebar hides the same links; this closes the direct-URL path. It is a
 * UX guard, not the security boundary — the backend refuses the data
 * regardless.
 */
export const permissionGuard: CanActivateFn = async (route) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  // Never decide against an empty set while boot is still fetching it.
  await auth.ensureLoaded();

  const required = route.data?.['requiredAction'] as string | undefined;
  if (required && auth.can(required)) {
    return true;
  }

  const fallback = firstVisiblePage((action) => auth.can(action));
  return router.parseUrl(fallback || 'no-access');
};
