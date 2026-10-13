import { Component, EventEmitter, Input, Output } from '@angular/core';

/**
 * The card shown while the app finds out who is here. A backend that
 * does not answer is waited on, and after a few tries the card says
 * so, with a way to ask again at once.
 */
@Component({
  selector: 'app-loading',
  standalone: false,
  templateUrl: './loading.component.html',
  styleUrls: ['./loading.component.css'],
})
export class LoadingComponent {
  /** The backend has not answered for a while. */
  @Input() unreachable = false;
  @Output() retry = new EventEmitter<void>();
}
