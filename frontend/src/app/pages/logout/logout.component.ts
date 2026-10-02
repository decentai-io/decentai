import { Component } from '@angular/core';
import { loginUrl } from '../../services/auth.util';

@Component({
  selector: 'app-logout',
  templateUrl: './logout.component.html',
  styleUrls: ['./logout.component.css'],
  standalone: false,
})
export class LogoutComponent {
  goToLogin(): void {
    // Reload the app: with no session, the shell shows the sign-in screen.
    window.location.href = loginUrl();
  }
}
