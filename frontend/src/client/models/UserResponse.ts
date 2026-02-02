/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UserRole } from './UserRole';
export type UserResponse = {
    personal_number: string;
    full_name: string;
    battalion: string;
    company: string;
    is_active_duty?: boolean;
    role?: UserRole;
    id: number;
};

