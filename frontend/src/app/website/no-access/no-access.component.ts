import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';

import { AuthService } from 'src/app/services/auth.service';
import { DataStoreService } from 'src/app/services/datastore.service';
import { NavigatorService } from 'src/app/services/navigator.service';
import { firstVisiblePage } from '../config/config';

/**
 * Where a signed-in user lands when their policy opens no page at all.
 * Rare, but the alternative is worse: a guard bouncing them between
 * denials with a blank screen and no explanation.
 *
 * If a page does become available — an administrator grants something and
 * the session is re-read — this page steps aside rather than trapping them.
 */
@Component({
  selector: 'app-no-access',
  templateUrl: './no-access.component.html',
  styleUrls: ['../page-not-found/page-not-found.component.css'],
  standalone: false,
})
export class NoAccessComponent implements OnInit {
  constructor(
    private router: Router,
    private auth: AuthService,
    private datastore: DataStoreService,
    private navigator: NavigatorService,
  ) {}

  async ngOnInit(): Promise<void> {
    this.datastore.changePageTitle('No access');

    // Read the session afresh: what was held may be out of date, and
    // this page is where an out-of-date answer would strand someone.
    await this.auth.refresh();
    const available = firstVisiblePage((action) => this.auth.can(action));
    if (available) {
      this.router.navigateByUrl(available);
    }
  }

  signOut(): void {
    // Ends the session on the server, then shows the signed-out screen.
    this.navigator.logout();
  }
}
