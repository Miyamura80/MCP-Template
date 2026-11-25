// pdfjs-dist ships no types for the worker entry; Signer.tsx only re-exposes it
// as globalThis.pdfjsWorker.
declare module "pdfjs-dist/build/pdf.worker.min.mjs";
