import { Component, EventEmitter, Input, Output } from '@angular/core';

export interface SharingGroup { group_id: string; group_name: string; }
export interface SharingPerson { user_id: string; user_name?: string; email?: string; }

@Component({
  selector: 'app-sharing-selector',
  standalone: false,
  templateUrl: './sharing-selector.component.html',
  styleUrls: ['./sharing-selector.component.css'],
})
export class SharingSelectorComponent {
  @Input() mode = 'private';
  @Input() groups: SharingGroup[] = [];
  @Input() people: SharingPerson[] = [];
  @Input() selectedGroups = new Set<string>();
  @Input() selectedPeople = new Set<string>();
  @Input() groupLabel = 'Specific groups';
  @Input() peopleMode = 'users';
  @Input() allowOrganization = true;
  @Input() disabled = false;
  @Input() disabledReason = 'Sharing is fixed for this item';

  @Output() modeChange = new EventEmitter<string>();
  @Output() groupToggled = new EventEmitter<string>();
  @Output() personToggled = new EventEmitter<string>();

  groupQuery = '';
  peopleQuery = '';

  get filteredGroups(): SharingGroup[] {
    const query = this.groupQuery.trim().toLowerCase();
    return query ? this.groups.filter((group) => group.group_name.toLowerCase().includes(query)) : this.groups;
  }

  get filteredPeople(): SharingPerson[] {
    const query = this.peopleQuery.trim().toLowerCase();
    return query ? this.people.filter((person) =>
      `${person.user_name || ''} ${person.email || ''}`.toLowerCase().includes(query)) : this.people;
  }

  choose(mode: string): void {
    if (!this.disabled) this.modeChange.emit(mode);
  }
}
