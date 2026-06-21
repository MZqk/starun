let sharedFile: File | null = null;

export const fileTransfer = {
  set(file: File) {
    sharedFile = file;
  },
  get() {
    const f = sharedFile;
    sharedFile = null;
    return f;
  },
};
