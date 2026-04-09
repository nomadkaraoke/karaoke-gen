import { render, screen, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NextIntlClientProvider } from 'next-intl';
import ReferralToolsDialog from '../ReferralToolsDialog';

// Mock qr-code-styling
const mockAppend = jest.fn();
const mockUpdate = jest.fn();
const mockDownload = jest.fn().mockResolvedValue(undefined);
const mockGetRawData = jest.fn().mockResolvedValue(new Blob(['fake'], { type: 'image/png' }));

jest.mock('qr-code-styling', () => {
  return jest.fn().mockImplementation(() => ({
    append: mockAppend,
    update: mockUpdate,
    download: mockDownload,
    getRawData: mockGetRawData,
  }));
});

// Mock html-to-image
jest.mock('html-to-image', () => ({
  toPng: jest.fn().mockResolvedValue('data:image/png;base64,fake'),
}));

// Mock jsPDF
jest.mock('jspdf', () => {
  return function JsPDFMock() {
    return {
      addImage: jest.fn(),
      save: jest.fn(),
      addPage: jest.fn(),
      internal: { pageSize: { getWidth: () => 215.9, getHeight: () => 279.4 } },
    };
  };
});

const messages = require('../../../messages/en.json');

function renderDialog(props: Partial<React.ComponentProps<typeof ReferralToolsDialog>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ReferralToolsDialog
        referralUrl="https://nomadkaraoke.com/r/testcode"
        open={true}
        onOpenChange={jest.fn()}
        referralCode="testcode"
        discountPercent={20}
        {...props}
      />
    </NextIntlClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('ReferralToolsDialog', () => {
  it('renders with tabs for QR Code and Flyer', () => {
    renderDialog();
    expect(screen.getByText('QR Code & Flyer Generator')).toBeInTheDocument();
    expect(screen.getByText('QR Code')).toBeInTheDocument();
    expect(screen.getByText('Printable Flyer')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    renderDialog({ open: false });
    expect(screen.queryByText('QR Code & Flyer Generator')).not.toBeInTheDocument();
  });

  it('shows QR Code tab content by default', () => {
    renderDialog();
    expect(screen.getByText('Dot Style')).toBeInTheDocument();
    expect(screen.getByText('Download PNG')).toBeInTheDocument();
    expect(screen.getByText('Download SVG')).toBeInTheDocument();
  });

  it('renders all QR dot style options on QR tab', () => {
    renderDialog();
    expect(screen.getAllByText('Square').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Rounded')).toBeInTheDocument();
    expect(screen.getByText('Dots')).toBeInTheDocument();
    expect(screen.getByText('Classy')).toBeInTheDocument();
    expect(screen.getByText('Classy Rounded')).toBeInTheDocument();
    expect(screen.getAllByText('Extra Rounded').length).toBeGreaterThanOrEqual(1);
  });

  it('renders corner frame and corner dot style sections', () => {
    renderDialog();
    expect(screen.getByText('Corner Frame')).toBeInTheDocument();
    expect(screen.getByText('Corner Dot')).toBeInTheDocument();
  });

  it('renders center logo options', () => {
    renderDialog();
    expect(screen.getByText('Center Logo')).toBeInTheDocument();
    expect(screen.getByText('None')).toBeInTheDocument();
    expect(screen.getByText('NK Logo')).toBeInTheDocument();
    expect(screen.getByText('Emoji')).toBeInTheDocument();
  });

  it('saves QR style prefs to localStorage on change', async () => {
    jest.useFakeTimers();
    renderDialog();
    fireEvent.click(screen.getByText('Rounded'));
    act(() => { jest.runAllTimers(); });
    const saved = JSON.parse(localStorage.getItem('nk-qr-style-prefs') || '{}');
    expect(saved.dotStyle).toBe('rounded');
    jest.useRealTimers();
  });

  it('restores QR style prefs from localStorage on open', () => {
    localStorage.setItem('nk-qr-style-prefs', JSON.stringify({
      dotStyle: 'dots',
      cornerSquareStyle: 'dot',
      cornerDotStyle: 'dot',
      fgColor: '#ff0000',
      bgColor: '#00ff00',
      logo: 'nomad',
      logoEmoji: '🎤',
    }));
    renderDialog();
    const fgInput = screen.getByDisplayValue('#ff0000');
    expect(fgInput).toBeInTheDocument();
  });

  it('switches to Flyer tab when clicked', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Download PDF')).toBeInTheDocument();
    expect(screen.getByText('Download HTML')).toBeInTheDocument();
  });

  it('renders flyer controls on Flyer tab', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Colors')).toBeInTheDocument();
    expect(screen.getByText('Sections')).toBeInTheDocument();
    expect(screen.getByText('Print Layout')).toBeInTheDocument();
  });

  it('shows flyer per-page options on Flyer tab', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Flyers Per Page')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '4' })).toBeInTheDocument();
  });

  it('renders section visibility toggles on Flyer tab', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Subtitle')).toBeInTheDocument();
    expect(screen.getByText('How It Works Steps')).toBeInTheDocument();
    expect(screen.getByText('Divider')).toBeInTheDocument();
    expect(screen.getByText('Bottom Features')).toBeInTheDocument();
    expect(screen.getByText('Bottom Tagline')).toBeInTheDocument();
  });

  it('shows Edit QR Style link on Flyer tab', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Edit QR Style')).toBeInTheDocument();
  });

  it('calls download with PNG extension when Download PNG clicked', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Download PNG'));
    // QR download should be triggered
    expect(mockDownload).toHaveBeenCalledWith({
      name: 'referral-qr',
      extension: 'png',
    });
  });

  it('calls download with SVG extension when Download SVG clicked', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Download SVG'));
    expect(mockDownload).toHaveBeenCalledWith({
      name: 'referral-qr',
      extension: 'svg',
    });
  });
});
