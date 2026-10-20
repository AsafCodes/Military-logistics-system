import type { MovementReportItem } from '@/types';

// What one movement-report row moved, for DailyActivityTable. Its own
// module because a component file that also exports functions loses fast
// refresh (react-refresh/only-export-components).

export const NONE = '—';

export type Move = { from: string; to: string; unit: { from: string; to: string } | null };

// DATA-M8-2. What a custody event moved, read off the from_/to_ names.
//
// A handover moves the item between holders, or between a holder and a
// place, so each end is the person if there is one and the place otherwise.
// An assignment moves the OWNER (the holder follows it), so its ends are the
// owners. The unit is shown only when it changed: most moves stay inside one
// company, and a line repeating the same unit twice would be noise.
//
// Null for every other event, and for a custody row with no to_ value at
// all, which is a row from before DATA-M8-1: the caller falls back to its
// `location`. A destination always has a to_ value on a new row, unless the
// person has a NULL full_name; that row reads as legacy too, and since a new
// row has no `location`, it shows the dash.
export function describeMove(item: MovementReportItem): Move | null {
    const type = item.event_type?.toLowerCase();
    let from: string | null;
    let to: string | null;
    if (type === 'handover' || type === 'handover_loc') {
        from = item.from_holder_name ?? item.from_location;
        to = item.to_holder_name ?? item.to_location;
    } else if (type === 'assign') {
        from = item.from_owner_name;
        to = item.to_owner_name;
    } else {
        return null;
    }
    if (to === null) return null;

    const unitChanged = item.from_group_name !== item.to_group_name;
    return {
        from: from ?? NONE,
        to,
        unit: unitChanged ? { from: item.from_group_name ?? NONE, to: item.to_group_name ?? NONE } : null,
    };
}
