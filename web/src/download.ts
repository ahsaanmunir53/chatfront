// Files the console hands the owner, such as shipping and return labels.

/** Save a file the page fetched: a link to it, clicked, then let go. */
export function saveFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
