/**
 * What a chat starts with where nobody chose, said once: the pages that
 * show it (a chat's menu, Chat defaults, Skills, the chat's skills
 * dialog) read it from here.
 */

/** How many skills a chat lists for the assistant each turn. */
export const STANDARD_SKILLS_LISTED = 40;

/** The choices of how many skills a chat lists; 0 is every one. */
export const SKILLS_LISTED_OPTIONS = [
  { rows: STANDARD_SKILLS_LISTED, label: 'Standard', hint: `${STANDARD_SKILLS_LISTED} skills listed` },
  { rows: 80, label: 'More', hint: '80 skills listed' },
  { rows: 160, label: 'Many', hint: '160 skills listed' },
  { rows: 0, label: 'All', hint: 'Every skill you can see' },
];
