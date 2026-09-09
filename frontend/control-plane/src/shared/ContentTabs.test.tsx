import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ContentTabs } from './ContentTabs';

it('moves selection and focus across enabled content tabs with arrow and endpoint keys', async () => {
  function Example() {
    const [value, setValue] = useState('files');
    return <ContentTabs label="Views" value={value} onChange={setValue} items={[
      { id: 'files', label: 'Files', tabId: 'files-tab', panelId: 'files-panel' },
      { id: 'blocked', label: 'Blocked', tabId: 'blocked-tab', panelId: 'blocked-panel', disabled: true },
      { id: 'config', label: 'Configuration', tabId: 'config-tab', panelId: 'config-panel' },
    ]} />;
  }
  render(<Example />);
  const files = screen.getByRole('tab', { name: 'Files' });
  const config = screen.getByRole('tab', { name: 'Configuration' });
  files.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(config).toHaveFocus();
  expect(config).toHaveAttribute('aria-selected', 'true');
  expect(config).toHaveAttribute('aria-controls', 'config-panel');
  await userEvent.keyboard('{Home}');
  expect(files).toHaveFocus();
  await userEvent.keyboard('{End}');
  expect(config).toHaveFocus();
});
