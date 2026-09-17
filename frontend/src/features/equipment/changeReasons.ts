/**
 * Why an equipment_status_history row exists, in Hebrew.
 *
 * DATA-H4-2. This vocabulary lived in THREE switch statements before this
 * module: two in EquipmentHistory.tsx (one for the icon, one for the label) and
 * a third in EquipmentPage.tsx's InlineHistory that fused the two and lived in
 * a different file entirely. DailyActivityTable.tsx had the same defect and
 * DATA-H4-1 collapsed it for the same reason -- parallel switches drift, and
 * these already had: the inline copy rendered '⚠️ תקלה' where the modal
 * rendered '⚠️ דיווח תקלה' for the identical row.
 *
 * Mirrors backend/enums.py ChangeReason, and tests/test_audit_trail.py asserts
 * every member there has an entry here -- so a backend reason added without a
 * translation fails the BACKEND suite, where the author is already looking.
 * That guard is the reason this is one map rather than two tidier switches: it
 * can only read one thing.
 *
 * No 'transfer' arm. All three switches carried one, for a value no router has
 * ever written and none ever will -- a transfer changes custody, not condition,
 * so it has no old_status/new_status to record and belongs in transaction_logs,
 * where it already is. Deleted rather than kept: ChangeReason omits TRANSFER
 * deliberately and permanently, so this is dead code, not a member waiting for
 * a writer.
 */
export type ReasonMeta = { icon: string; label: string };

// Module-private on purpose: reasonMeta below is the only way in, so no caller
// can index the map directly and skip the fallback. The backend guard reads
// this file's source text for `const REASON_META`, which is here either way.
const REASON_META: Record<string, ReasonMeta> = {
    verification: { icon: '✅', label: 'אימות' },
    fault_report: { icon: '⚠️', label: 'דיווח תקלה' },
    repair: { icon: '🔧', label: 'תיקון' },
};

/**
 * The fallback is the raw reason string beside a neutral icon, NOT a guess.
 *
 * A value reaching here means the backend wrote a reason this build does not
 * know, and showing the operator what the row actually says is the only honest
 * option -- the alternative, mapping the unknown onto a nearby label, invents
 * an audit finding. Reachable in one real case: a frontend deployed behind a
 * newer backend.
 */
export function reasonMeta(reason: string): ReasonMeta {
    return REASON_META[reason] ?? { icon: '📝', label: reason };
}
