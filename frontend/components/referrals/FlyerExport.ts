import html2canvas from 'html2canvas';
import jsPDF from 'jspdf';

// US Letter in mm
const PAGE_W_MM = 215.9;
const PAGE_H_MM = 279.4;

interface PdfOptions {
  perPage: 1 | 2 | 4;
  marginMm: number;
  filename: string;
}

export async function exportFlyerPdf(
  flyerElement: HTMLElement,
  options: PdfOptions,
): Promise<void> {
  const { perPage, marginMm, filename } = options;

  const canvas = await html2canvas(flyerElement, {
    scale: 2,
    useCORS: true,
    allowTaint: true,
    backgroundColor: null,
  });

  const imgData = canvas.toDataURL('image/png');
  const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' });

  const margin = marginMm;
  const usableW = PAGE_W_MM - margin * 2;
  const usableH = PAGE_H_MM - margin * 2;
  const flyerAspect = 8.5 / 11;

  if (perPage === 1) {
    let w = usableW;
    let h = w / flyerAspect;
    if (h > usableH) {
      h = usableH;
      w = h * flyerAspect;
    }
    const x = margin + (usableW - w) / 2;
    const y = margin + (usableH - h) / 2;
    pdf.addImage(imgData, 'PNG', x, y, w, h);
  } else if (perPage === 2) {
    const gapMm = margin;
    const slotH = (usableH - gapMm) / 2;
    let w = usableW;
    let h = w / flyerAspect;
    if (h > slotH) {
      h = slotH;
      w = h * flyerAspect;
    }
    const x = margin + (usableW - w) / 2;
    pdf.addImage(imgData, 'PNG', x, margin, w, h);
    pdf.addImage(imgData, 'PNG', x, margin + slotH + gapMm, w, h);
  } else {
    const gapMm = margin;
    const slotW = (usableW - gapMm) / 2;
    const slotH = (usableH - gapMm) / 2;
    let w = slotW;
    let h = w / flyerAspect;
    if (h > slotH) {
      h = slotH;
      w = h * flyerAspect;
    }
    const positions = [
      [margin, margin],
      [margin + slotW + gapMm, margin],
      [margin, margin + slotH + gapMm],
      [margin + slotW + gapMm, margin + slotH + gapMm],
    ];
    for (const [px, py] of positions) {
      const offsetX = px + (slotW - w) / 2;
      const offsetY = py + (slotH - h) / 2;
      pdf.addImage(imgData, 'PNG', offsetX, offsetY, w, h);
    }
  }

  pdf.save(filename);
}

export function exportFlyerHtml(flyerElement: HTMLElement): Blob {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nomad Karaoke Referral Flyer</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*, html { margin: 0; padding: 0; box-sizing: border-box; }
@page { size: 8.5in 11in; margin: 0; }
body { display: flex; justify-content: center; }
@media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
</style>
</head>
<body>
${flyerElement.outerHTML}
</body>
</html>`;
  return new Blob([html], { type: 'text/html' });
}

export async function exportFlyerPng(
  flyerElement: HTMLElement,
  filename: string,
): Promise<void> {
  const canvas = await html2canvas(flyerElement, {
    scale: 2,
    useCORS: true,
    allowTaint: true,
    backgroundColor: null,
  });

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b: Blob | null) => {
      if (b) resolve(b);
      else reject(new Error('Failed to export flyer as PNG'));
    }, 'image/png');
  });

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
