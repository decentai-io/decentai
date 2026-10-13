import { ActivitySchedule } from 'src/app/services/activity.service';
import { alsoOf, failuresOf, stateOf, whatOf } from './schedule-shared';

const schedule = (more: Partial<ActivitySchedule>): ActivitySchedule => ({
  schedule_id: 'sch_1', chat_id: 'chat_1', chat_title: 'Reports', mode: 'wake', function: '', inputs: {},
  wake_field: '', note: 'Send the report', every_seconds: null, cron: '0 9 * * *', timezone: 'UTC',
  next_run_at: 0, enabled: true, last_run_at: null, runs: [], ...more,
});
const run = (status: string) => ({ at: 1, status, woke: false });

describe('a schedule in words', () => {
  it('says how it stands', () => {
    expect(stateOf(schedule({}))).toBe('active');
    expect(stateOf(schedule({ enabled: false }))).toBe('paused');
    // A one-off that ran is done, not paused; one that never ran is paused.
    expect(stateOf(schedule({ enabled: false, cron: '', last_run_at: 5 }))).toBe('done');
    expect(stateOf(schedule({ enabled: false, cron: '' }))).toBe('paused');
    // Failing is about the last run, and only while it is still on.
    expect(stateOf(schedule({ runs: [run('error'), run('success')] }))).toBe('active');
    expect(stateOf(schedule({ runs: [run('success'), run('error')] }))).toBe('failing');
    expect(stateOf(schedule({ enabled: false, runs: [run('error')] }))).toBe('paused');
  });

  it('names a function by what its agent calls it', () => {
    const functions = [{ value: 'notebook.note.find', label: 'Notebook · Find Notes', agent: 'notebook', inputs: {}, outputs: ['notes'] }];
    const row = schedule({ note: '', function: 'notebook.note.find', wake_field: 'notes' });
    expect(whatOf(row, functions)).toBe('Run Notebook · Find Notes');
    expect(whatOf(row, [])).toBe('Run note.find');
    expect(alsoOf(row, functions)).toBe('wakes the assistant when notes comes back');
    expect(alsoOf(schedule({}), functions)).toBe('');
  });

  it('counts the runs that failed', () => {
    expect(failuresOf(schedule({ runs: [run('error'), run('success'), run('woke')] }))).toBe('1 of 3 failed');
    expect(failuresOf(schedule({ runs: [run('success')] }))).toBe('');
  });
});
