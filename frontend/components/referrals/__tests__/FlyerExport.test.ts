import { exportFlyerPdf, exportFlyerHtml, exportFlyerPng } from '../FlyerExport';

// Mock html2canvas — factory must be self-contained (jest hoists it)
jest.mock('html2canvas', () => {
  return jest.fn().mockResolvedValue({
    toDataURL: jest.fn().mockReturnValue('data:image/png;base64,fakepng'),
    toBlob: jest.fn((cb: (blob: Blob) => void) => cb(new Blob(['png'], { type: 'image/png' }))),
    width: 1632,
    height: 2112,
  });
});

// Mock jsPDF
const mockAddImage = jest.fn();
const mockSave = jest.fn();
jest.mock('jspdf', () => {
  // Must use require-style access for outer variables with jest.mock hoisting
  return function JsPDFMock() {
    return {
      addImage: (...args: unknown[]) => mockAddImage(...args),
      save: (...args: unknown[]) => mockSave(...args),
      addPage: jest.fn(),
      internal: { pageSize: { getWidth: () => 215.9, getHeight: () => 279.4 } },
    };
  };
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe('FlyerExport', () => {
  const mockElement = document.createElement('div');
  mockElement.setAttribute('data-flyer-page', '');
  Object.defineProperty(mockElement, 'outerHTML', { value: '<div data-flyer-page="">test</div>' });

  describe('exportFlyerPdf', () => {
    it('generates PDF with 1 per page', async () => {
      await exportFlyerPdf(mockElement, { perPage: 1, marginMm: 15, filename: 'test.pdf' });
      expect(mockAddImage).toHaveBeenCalledTimes(1);
      expect(mockSave).toHaveBeenCalledWith('test.pdf');
    });

    it('generates PDF with 2 per page', async () => {
      await exportFlyerPdf(mockElement, { perPage: 2, marginMm: 10, filename: 'test-2up.pdf' });
      expect(mockAddImage).toHaveBeenCalledTimes(2);
      expect(mockSave).toHaveBeenCalledWith('test-2up.pdf');
    });

    it('generates PDF with 4 per page', async () => {
      await exportFlyerPdf(mockElement, { perPage: 4, marginMm: 5, filename: 'test-4up.pdf' });
      expect(mockAddImage).toHaveBeenCalledTimes(4);
      expect(mockSave).toHaveBeenCalledWith('test-4up.pdf');
    });
  });

  describe('exportFlyerHtml', () => {
    it('returns a blob containing the flyer HTML', () => {
      const blob = exportFlyerHtml(mockElement);
      expect(blob).toBeInstanceOf(Blob);
      expect(blob.type).toBe('text/html');
    });
  });

  describe('exportFlyerPng', () => {
    it('calls html2canvas and triggers download', async () => {
      const mockUrl = 'blob:test-png';
      global.URL.createObjectURL = jest.fn().mockReturnValue(mockUrl);
      global.URL.revokeObjectURL = jest.fn();

      const mockClick = jest.fn();
      const origCreateElement = document.createElement.bind(document);
      jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        const el = origCreateElement(tag);
        if (tag === 'a') {
          el.click = mockClick;
        }
        return el;
      });

      await exportFlyerPng(mockElement, 'test.png');

      const html2canvas = require('html2canvas');
      expect(html2canvas).toHaveBeenCalledWith(mockElement, expect.objectContaining({ scale: 2 }));
      expect(mockClick).toHaveBeenCalled();

      jest.restoreAllMocks();
    });
  });
});
