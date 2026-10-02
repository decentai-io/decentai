import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { AuthService } from 'src/app/services/auth.service';
import { DataStoreService } from 'src/app/services/datastore.service';
import { firstVisiblePage } from '../config/config';

@Component({
  selector: 'app-page-not-found',
  templateUrl: './page-not-found.component.html',
  styleUrls: ['./page-not-found.component.css'],
  standalone: false,
})
export class PageNotFoundComponent implements OnInit {
  constructor(
    private router: Router,
    private auth: AuthService,
    public datastore: DataStoreService,
  ) {}

  ngOnInit(): void {
    // Reset the header to the default page (changePageTitle persists it too).
    this.datastore.changePageTitle('Page not found');
  }

  goHome(): void {
    const available = firstVisiblePage((action) => this.auth.can(action));
    this.router.navigateByUrl(available || 'no-access');
  }
}
