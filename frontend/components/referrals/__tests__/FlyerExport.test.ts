import { exportFlyerPdf, exportFlyerHtml, exportFlyerPng } from '../FlyerExport';

// Mock html-to-image
jest.mock('html-to-image', () => ({
  toPng: jest.fn().mockResolvedValue('data:image/png;base64,fakepng'),
}));

// Mock jsPDF
const mockAddImage = jest.fn();
const mockSave = jest.fn();
jest.mock('jspdf', () => {
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
    it('calls toPng and triggers download', async () => {
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

      const { toPng } = require('html-to-image');
      expect(toPng).toHaveBeenCalledWith(mockElement, { pixelRatio: 2, width: 816, height: 1056 });
      expect(mockClick).toHaveBeenCalled();

      jest.restoreAllMocks();
    });
  });
});
