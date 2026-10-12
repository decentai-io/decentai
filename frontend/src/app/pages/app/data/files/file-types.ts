/**
 * What kind of thing a file is, told from its name and, failing that,
 * its MIME type: the nine kinds a file's icon distinguishes, and the
 * six groups the Files page filters by.
 */

export type FileKind =
  | 'document' | 'spreadsheet' | 'presentation' | 'pdf' | 'image'
  | 'code' | 'media' | 'archive' | 'file';

const KIND_BY_EXTENSION: Record<string, FileKind> = {};
function extensions(kind: FileKind, list: string): void {
  for (const extension of list.split(' ')) KIND_BY_EXTENSION[extension] = kind;
}
extensions('document', 'doc docx odt rtf txt md markdown');
extensions('spreadsheet', 'xls xlsx ods csv tsv');
extensions('presentation', 'ppt pptx odp key');
extensions('pdf', 'pdf');
extensions('image', 'png jpg jpeg gif webp svg bmp heic tiff');
extensions('code', 'js ts py json yaml yml html css scss sh sql xml java go rs c cpp rb php toml ipynb');
extensions('media', 'mp3 wav ogg m4a flac mp4 mov webm mkv avi');
extensions('archive', 'zip tar gz tgz rar 7z');

/** The MIME types that say the kind when the name does not. */
const KIND_BY_MIME: [prefix: string, kind: FileKind][] = [
  ['image/', 'image'],
  ['audio/', 'media'],
  ['video/', 'media'],
  ['application/pdf', 'pdf'],
  ['text/csv', 'spreadsheet'],
  ['text/', 'document'],
];

/** The extension of a file's name, lower-case, or ''. */
export function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot > 0 && dot < filename.length - 1 ? filename.slice(dot + 1).toLowerCase() : '';
}

export function fileKindOf(filename: string, mime = ''): FileKind {
  const byName = KIND_BY_EXTENSION[extensionOf(filename)];
  if (byName) return byName;
  return KIND_BY_MIME.find(([prefix]) => mime.startsWith(prefix))?.[1] ?? 'file';
}

/** One of the groups the Files page filters by. */
export interface FileGroup {
  value: string;
  name: string;
  /** Its Lucide icon. */
  icon: string;
  kinds: FileKind[];
}

export const FILE_GROUPS: FileGroup[] = [
  { value: 'documents', name: 'Documents', icon: 'file-text', kinds: ['document', 'presentation', 'pdf'] },
  { value: 'images', name: 'Images', icon: 'file-image', kinds: ['image'] },
  { value: 'sheets', name: 'Sheets', icon: 'file-spreadsheet', kinds: ['spreadsheet'] },
  { value: 'code', name: 'Code', icon: 'file-code', kinds: ['code'] },
  { value: 'media', name: 'Audio and video', icon: 'file-music', kinds: ['media'] },
  { value: 'other', name: 'Other', icon: 'file-archive', kinds: ['archive', 'file'] },
];

/** A file's size in the unit that reads best: "1.4 MB"; '' when unknown. */
export function sizeLabel(bytes: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
