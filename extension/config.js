// Overwritten at package time by tools/package.sh. See its --erp-origin flag.
//
// A built copy carries the address of the bench it was built for, so whoever
// receives it has nothing to paste. This committed version is deliberately
// empty: the repository is public, and a tunnel address is a live way in to a
// bench holding real fleet data. It belongs in the zip, not in the source.
//
// null means "ask the user", which is what the options page does.
globalThis.TFF_DEFAULT_ERP_ORIGIN = null;
globalThis.TFF_KNOWN_ERP_ORIGINS = [];
