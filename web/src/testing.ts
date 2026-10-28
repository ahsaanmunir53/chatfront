// Stand-ins for browser APIs in unit tests, which run without a browser.

/** localStorage or sessionStorage, in memory. */
export class MemoryStorage {
  private items = new Map<string, string>();
  getItem(name: string): string | null {
    return this.items.get(name) ?? null;
  }
  setItem(name: string, value: string): void {
    this.items.set(name, value);
  }
  removeItem(name: string): void {
    this.items.delete(name);
  }
  clear(): void {
    this.items.clear();
  }
}
