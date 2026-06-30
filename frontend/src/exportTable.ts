export type ExportCellValue = string | number | boolean | null | undefined;

export type ExportCellStyle = "text" | "integer" | "decimal2" | "decimal3" | "percent1";
export type ExportRowStyle = "bold";

export interface ExportColumn<Row> {
  header: string;
  value: (row: Row) => ExportCellValue;
  csvValue?: (row: Row) => ExportCellValue;
  width?: number;
  style?: ExportCellStyle;
}

export interface ExportXlsxOptions<Row> {
  rowStyle?: (row: Row) => ExportRowStyle | undefined;
}

interface SheetCell {
  value: ExportCellValue;
  style?: ExportCellStyle | "header";
  rowStyle?: ExportRowStyle;
}

const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const CSV_MIME = "text/csv;charset=utf-8";

const STYLE_IDS: Record<ExportCellStyle | "header", number> = {
  text: 0,
  header: 1,
  percent1: 2,
  decimal3: 3,
  decimal2: 4,
  integer: 5
};

const BOLD_STYLE_IDS: Record<ExportCellStyle, number> = {
  text: 6,
  percent1: 7,
  decimal3: 8,
  decimal2: 9,
  integer: 10
};

export function downloadCsv<Row>(filename: string, columns: ExportColumn<Row>[], rows: Row[]) {
  const lines = [
    columns.map((column) => serializeCsvCell(column.header)).join(","),
    ...rows.map((row) =>
      columns
        .map((column) => serializeCsvCell((column.csvValue || column.value)(row)))
        .join(",")
    )
  ];
  const blob = new Blob([`\uFEFF${lines.join("\r\n")}\r\n`], { type: CSV_MIME });
  downloadBlob(blob, ensureExtension(filename, "csv"));
}

export function downloadXlsx<Row>(
  filename: string,
  sheetName: string,
  columns: ExportColumn<Row>[],
  rows: Row[],
  options: ExportXlsxOptions<Row> = {}
) {
  const sheetRows: SheetCell[][] = [
    columns.map((column) => ({ value: column.header, style: "header" })),
    ...rows.map((row) => {
      const rowStyle = options.rowStyle?.(row);
      return columns.map((column) => ({
        value: column.value(row),
        style: column.style,
        rowStyle
      }));
    })
  ];
  const worksheet = buildWorksheetXml(sheetRows, columns.map((column) => column.width || 14));
  const workbook = buildWorkbookXml(sheetName);
  const zip = createZip([
    { name: "[Content_Types].xml", data: contentTypesXml },
    { name: "_rels/.rels", data: rootRelsXml },
    { name: "docProps/app.xml", data: appPropsXml },
    { name: "docProps/core.xml", data: corePropsXml() },
    { name: "xl/workbook.xml", data: workbook },
    { name: "xl/_rels/workbook.xml.rels", data: workbookRelsXml },
    { name: "xl/styles.xml", data: stylesXml },
    { name: "xl/worksheets/sheet1.xml", data: worksheet }
  ]);
  const blob = new Blob([zip], { type: XLSX_MIME });
  downloadBlob(blob, ensureExtension(filename, "xlsx"));
}

export function safeFileStem(value: string) {
  return (
    value
      .trim()
      .replace(/[\\/:*?"<>|]+/g, "-")
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 96) || "export"
  );
}

function serializeCsvCell(value: ExportCellValue) {
  if (value === null || value === undefined) return "";
  const text = String(value);
  if (!/[",\r\n]/.test(text)) return text;
  return `"${text.replace(/"/g, '""')}"`;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function ensureExtension(filename: string, extension: string) {
  const normalized = filename.trim() || `export.${extension}`;
  return normalized.toLowerCase().endsWith(`.${extension}`) ? normalized : `${normalized}.${extension}`;
}

function buildWorksheetXml(rows: SheetCell[][], widths: number[]) {
  const columnCount = Math.max(widths.length, rows[0]?.length || 0);
  const rowCount = Math.max(rows.length, 1);
  const lastCell = `${columnName(columnCount - 1)}${rowCount}`;
  const cols = widths
    .map((width, index) => {
      const columnIndex = index + 1;
      return `<col min="${columnIndex}" max="${columnIndex}" width="${Math.max(8, width)}" customWidth="1"/>`;
    })
    .join("");
  const sheetData = rows
    .map((row, rowIndex) => {
      const number = rowIndex + 1;
      const cells = row.map((cell, columnIndex) => buildCellXml(cell, number, columnIndex)).join("");
      return `<row r="${number}">${cells}</row>`;
    })
    .join("");

  return xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft"/>
    </sheetView>
  </sheetViews>
  <cols>${cols}</cols>
  <sheetData>${sheetData}</sheetData>
  <autoFilter ref="A1:${lastCell}"/>
</worksheet>`);
}

function buildCellXml(cell: SheetCell, rowIndex: number, columnIndex: number) {
  const ref = `${columnName(columnIndex)}${rowIndex}`;
  const styleId = cellStyleId(cell);
  const styleAttr = styleId ? ` s="${styleId}"` : "";
  const value = cell.value;
  if (value === null || value === undefined || value === "") {
    return `<c r="${ref}"${styleAttr}/>`;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return `<c r="${ref}"${styleAttr} t="n"><v>${value}</v></c>`;
  }
  if (typeof value === "boolean") {
    return `<c r="${ref}"${styleAttr} t="b"><v>${value ? 1 : 0}</v></c>`;
  }
  const text = String(value);
  const preserve = /^\s|\s$|\r|\n/.test(text) ? ' xml:space="preserve"' : "";
  return `<c r="${ref}"${styleAttr} t="inlineStr"><is><t${preserve}>${escapeXml(text)}</t></is></c>`;
}

function cellStyleId(cell: SheetCell) {
  if (cell.style === "header") return STYLE_IDS.header;
  const style = cell.style || "text";
  if (cell.rowStyle === "bold") return BOLD_STYLE_IDS[style];
  return STYLE_IDS[style];
}

function buildWorkbookXml(sheetName: string) {
  const safeSheetName = sanitizeSheetName(sheetName);
  return xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="${escapeXml(safeSheetName)}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>`);
}

function sanitizeSheetName(sheetName: string) {
  const cleaned = sheetName.replace(/[\\/?*[\]:]/g, " ").trim();
  return (cleaned || "Sheet1").slice(0, 31);
}

function columnName(index: number) {
  let name = "";
  let n = Math.max(0, index) + 1;
  while (n > 0) {
    const remainder = (n - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

function escapeXml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function xmlDeclaration(value: string) {
  return value.trim();
}

const contentTypesXml = xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>`);

const rootRelsXml = xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>`);

const workbookRelsXml = xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`);

const appPropsXml = xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Dify-KB-Eval</Application>
</Properties>`);

function corePropsXml() {
  const now = new Date().toISOString();
  return xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Dify-KB-Eval</dc:creator>
  <cp:lastModifiedBy>Dify-KB-Eval</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">${now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">${now}</dcterms:modified>
</cp:coreProperties>`);
}

const stylesXml = xmlDeclaration(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="3">
    <numFmt numFmtId="164" formatCode="0.0%"/>
    <numFmt numFmtId="165" formatCode="0.000"/>
    <numFmt numFmtId="166" formatCode="0.00"/>
  </numFmts>
  <fonts count="2">
    <font><sz val="11"/><color rgb="FF1E2E44"/><name val="Microsoft YaHei UI"/></font>
    <font><b/><sz val="11"/><color rgb="FF1E2E44"/><name val="Microsoft YaHei UI"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEFF6FF"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFD9E2EF"/></left>
      <right style="thin"><color rgb="FFD9E2EF"/></right>
      <top style="thin"><color rgb="FFD9E2EF"/></top>
      <bottom style="thin"><color rgb="FFD9E2EF"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="11">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="1" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
    <xf numFmtId="164" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="165" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="166" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="1" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyNumberFormat="1" applyBorder="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>`);

interface ZipFile {
  name: string;
  data: string | Uint8Array;
}

function createZip(files: ZipFile[]) {
  const encoder = new TextEncoder();
  const localChunks: Uint8Array[] = [];
  const centralChunks: Uint8Array[] = [];
  const offsets: number[] = [];
  const { date, time } = dosDateTime(new Date());
  let offset = 0;

  files.forEach((file) => {
    const nameBytes = encoder.encode(file.name);
    const dataBytes = typeof file.data === "string" ? encoder.encode(file.data) : file.data;
    const crc = crc32(dataBytes);
    offsets.push(offset);

    const localHeader = new Uint8Array(30);
    const local = new DataView(localHeader.buffer);
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true);
    local.setUint16(8, 0, true);
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, dataBytes.length, true);
    local.setUint32(22, dataBytes.length, true);
    local.setUint16(26, nameBytes.length, true);
    local.setUint16(28, 0, true);
    localChunks.push(localHeader, nameBytes, dataBytes);
    offset += localHeader.length + nameBytes.length + dataBytes.length;

    const centralHeader = new Uint8Array(46);
    const central = new DataView(centralHeader.buffer);
    central.setUint32(0, 0x02014b50, true);
    central.setUint16(4, 20, true);
    central.setUint16(6, 20, true);
    central.setUint16(8, 0x0800, true);
    central.setUint16(10, 0, true);
    central.setUint16(12, time, true);
    central.setUint16(14, date, true);
    central.setUint32(16, crc, true);
    central.setUint32(20, dataBytes.length, true);
    central.setUint32(24, dataBytes.length, true);
    central.setUint16(28, nameBytes.length, true);
    central.setUint16(30, 0, true);
    central.setUint16(32, 0, true);
    central.setUint16(34, 0, true);
    central.setUint16(36, 0, true);
    central.setUint32(38, 0, true);
    central.setUint32(42, offsets[offsets.length - 1], true);
    centralChunks.push(centralHeader, nameBytes);
  });

  const centralOffset = offset;
  const centralSize = centralChunks.reduce((total, chunk) => total + chunk.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(4, 0, true);
  endView.setUint16(6, 0, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, centralOffset, true);
  endView.setUint16(20, 0, true);

  return concatUint8Arrays([...localChunks, ...centralChunks, end]);
}

function concatUint8Arrays(chunks: Uint8Array[]) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const output = new Uint8Array(length);
  let offset = 0;
  chunks.forEach((chunk) => {
    output.set(chunk, offset);
    offset += chunk.length;
  });
  return output;
}

function dosDateTime(date: Date) {
  const year = Math.max(1980, date.getFullYear());
  return {
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
    date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
  };
}

const crcTable = new Uint32Array(256);
for (let i = 0; i < 256; i += 1) {
  let c = i;
  for (let k = 0; k < 8; k += 1) {
    c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  }
  crcTable[i] = c >>> 0;
}

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  bytes.forEach((byte) => {
    crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  });
  return (crc ^ 0xffffffff) >>> 0;
}
