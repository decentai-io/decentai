/**
 * The keyboard of a group of radios, whatever it looks like (the
 * segmented control, the choice cards): the arrows move the choice,
 * Home and End go to the ends, and it wraps. Returns the index the key
 * leads to, or null when the key is not one of them.
 */
export function radioKeyTarget(key: string, index: number, length: number): number | null {
  switch (key) {
    case 'ArrowRight':
    case 'ArrowDown':
      return (index + 1) % length;
    case 'ArrowLeft':
    case 'ArrowUp':
      return (index - 1 + length) % length;
    case 'Home':
      return 0;
    case 'End':
      return length - 1;
    default:
      return null;
  }
}

/** One choice in a group: its value, its name, and what it means. */
export interface Choice<T = string> {
  value: T;
  name: string;
  /** One line under the name. */
  hint?: string;
  disabled?: boolean;
}
