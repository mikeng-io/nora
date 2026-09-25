interface ImportMetaEnv {
  /** Origin of the portal, if it is not this site's own (site/README.md). */
  readonly PUBLIC_PORTAL_ORIGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
