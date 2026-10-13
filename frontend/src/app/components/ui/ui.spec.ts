import { Component, SimpleChange } from '@angular/core';
import { fakeAsync, TestBed, tick } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { FieldComponent } from './field.component';
import { radioKeyTarget } from './radio-keys';
import { SaveBarComponent } from './save-bar.component';
import { SegmentedComponent } from './segmented.component';
import { TokenListComponent } from './token-list.component';

describe('radioKeyTarget', () => {
  it('moves with the arrows and wraps at the ends', () => {
    expect(radioKeyTarget('ArrowRight', 0, 3)).toBe(1);
    expect(radioKeyTarget('ArrowDown', 2, 3)).toBe(0);
    expect(radioKeyTarget('ArrowLeft', 0, 3)).toBe(2);
    expect(radioKeyTarget('Home', 2, 3)).toBe(0);
    expect(radioKeyTarget('End', 0, 3)).toBe(2);
  });

  it('leaves every other key alone', () => {
    expect(radioKeyTarget('Enter', 1, 3)).toBeNull();
  });
});

describe('SegmentedComponent', () => {
  it('tells the form once per change, and not when the choice is the same', () => {
    const segmented = new SegmentedComponent<string>({ nativeElement: document.createElement('div') });
    segmented.choices = [{ value: 'a', name: 'A' }, { value: 'b', name: 'B' }];
    const heard: string[] = [];
    segmented.registerOnChange((value) => heard.push(value));
    segmented.writeValue('a');

    segmented.choose('a');
    segmented.choose('b');

    expect(heard).toEqual(['b']);
  });
});

describe('TokenListComponent', () => {
  function list(tokens: string[]) {
    const component = new TokenListComponent();
    const heard: string[][] = [];
    component.registerOnChange((value) => heard.push(value));
    component.writeValue(tokens);
    return { component, heard };
  }

  it('adds what is typed, trimmed, and clears the input', () => {
    const { component, heard } = list(['a.test']);
    component.draft = '  b.test ';

    component.add();

    expect(heard).toEqual([['a.test', 'b.test']]);
    expect(component.draft).toBe('');
  });

  it('does not add a value twice, or an empty one', () => {
    const { component, heard } = list(['a.test']);

    component.draft = 'a.test';
    component.add();
    component.draft = '   ';
    component.add();

    expect(heard).toEqual([]);
  });

  it('removes a value', () => {
    const { component, heard } = list(['a.test', 'b.test']);

    component.remove('a.test');

    expect(heard).toEqual([['b.test']]);
  });

  it('does not change the list it was given', () => {
    const given = ['a.test'];
    const { component } = list(given);
    component.draft = 'b.test';

    component.add();

    expect(given).toEqual(['a.test']);
  });
});

describe('SaveBarComponent', () => {
  /** Sets `saving` the way a template binding would. */
  function setSaving(bar: SaveBarComponent, saving: boolean): void {
    const before = bar.saving;
    bar.saving = saving;
    bar.ngOnChanges({ saving: new SimpleChange(before, saving, false) });
  }

  it('is not shown while nothing changed', () => {
    expect(new SaveBarComponent().shown).toBe(false);
  });

  it('says Saved for two seconds after a save that worked, then leaves', fakeAsync(() => {
    const bar = new SaveBarComponent();
    bar.dirty = true;
    setSaving(bar, true);

    bar.dirty = false;
    setSaving(bar, false);
    expect(bar.saved).toBe(true);
    expect(bar.shown).toBe(true);

    tick(2000);
    expect(bar.shown).toBe(false);
  }));

  it('stays, without Saved, when the save failed', () => {
    const bar = new SaveBarComponent();
    bar.dirty = true;
    setSaving(bar, true);

    bar.error = 'The server refused it.';
    setSaving(bar, false);

    expect(bar.saved).toBe(false);
    expect(bar.shown).toBe(true);
  });

  it('saves on Ctrl+S only while there is something to save', () => {
    const bar = new SaveBarComponent();
    let saves = 0;
    bar.save.subscribe(() => (saves += 1));
    const press = () => bar.onKey(new KeyboardEvent('keydown', { key: 's', ctrlKey: true }));

    press();
    bar.dirty = true;
    press();

    expect(saves).toBe(1);
  });
});

@Component({
  standalone: true,
  imports: [FieldComponent],
  template: `
    <app-field label="Name" [help]="help" [error]="error"><input /></app-field>
  `,
})
class FieldHost {
  help = 'Shown in the list.';
  error = '';
}

describe('FieldComponent', () => {
  it('names its control with the label and describes it with the help, then the error', () => {
    TestBed.configureTestingModule({ imports: [FieldHost], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(FieldHost);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    const input = root.querySelector('input')!;
    const label = root.querySelector('label')!;

    expect(label.getAttribute('for')).toBe(input.id);
    expect(input.getAttribute('aria-describedby')).toBe(root.querySelector('.field-help')!.id);
    expect(input.hasAttribute('aria-invalid')).toBe(false);

    fixture.componentInstance.error = 'A name is needed.';
    fixture.detectChanges();

    expect(root.querySelector('.field-help')).toBeNull();
    expect(input.getAttribute('aria-describedby')).toBe(root.querySelector('.field-error')!.id);
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });
});
