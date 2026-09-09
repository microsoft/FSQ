import { StrictMode, useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DialogFrame } from './DialogFrame';

it('restores the connected opener after StrictMode effect replay and Escape', async () => {
  function Example() {
    const [open, setOpen] = useState(false);
    return <><button onClick={() => setOpen(true)}>Open dialog</button>{open && <DialogFrame title="Review" onClose={() => setOpen(false)}><button>First</button><button>Last</button></DialogFrame>}</>;
  }
  render(<StrictMode><Example /></StrictMode>);
  await userEvent.click(screen.getByRole('button', {name:'Open dialog'}));
  await waitFor(() => expect(screen.getByRole('button', {name:'First'})).toHaveFocus());
  await userEvent.tab({shift:true});
  expect(screen.getByRole('button', {name:'Last'})).toHaveFocus();
  await userEvent.keyboard('{Escape}');
  await waitFor(() => expect(screen.getByRole('button', {name:'Open dialog'})).toHaveFocus());
});

it('retains modal focus when asynchronous content removes the focused control', async () => {
  function Example() {
    const [pending, setPending] = useState(false);
    return <><button>Background</button><DialogFrame title="Connect" onClose={() => {}}>{pending ? <><p>Requesting code…</p><button>Cancel request</button></> : <button onClick={() => setPending(true)}>Connect provider</button>}</DialogFrame></>;
  }
  render(<Example/>);
  await userEvent.click(screen.getByRole('button',{name:'Connect provider'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Cancel request'})).toHaveFocus());
  await userEvent.tab();
  expect(screen.getByRole('button',{name:'Cancel request'})).toHaveFocus();
});
