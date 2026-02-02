/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssignOwnerRequest } from '../models/AssignOwnerRequest';
import type { Body_login_for_access_token_login_post } from '../models/Body_login_for_access_token_login_post';
import type { EquipmentCreate } from '../models/EquipmentCreate';
import type { EquipmentResponse } from '../models/EquipmentResponse';
import type { PromoteUserRequest } from '../models/PromoteUserRequest';
import type { TicketResponse } from '../models/TicketResponse';
import type { Token } from '../models/Token';
import type { UnitReadinessResponse } from '../models/UnitReadinessResponse';
import type { UserCreate } from '../models/UserCreate';
import type { UserResponse } from '../models/UserResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * Login For Access Token
     * @param formData
     * @returns Token Successful Response
     * @throws ApiError
     */
    public static loginForAccessTokenLoginPost(
        formData: Body_login_for_access_token_login_post,
    ): CancelablePromise<Token> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/login',
            formData: formData,
            mediaType: 'application/x-www-form-urlencoded',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Root
     * @returns any Successful Response
     * @throws ApiError
     */
    public static readRootGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/',
        });
    }
    /**
     * Initialize System
     * @returns any Successful Response
     * @throws ApiError
     */
    public static initializeSystemSetupInitializeSystemPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/setup/initialize_system',
        });
    }
    /**
     * Create User
     * Register a new user.
     * - Password is hashed.
     * - First user ever -> MASTER.
     * - All others -> USER.
     * @param requestBody
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static createUserUsersPost(
        requestBody: UserCreate,
    ): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/users/',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Promote User
     * Promote a user.
     * Requirement: Current user must be MASTER.
     * @param requestBody
     * @returns UserResponse Successful Response
     * @throws ApiError
     */
    public static promoteUserUsersPromotePut(
        requestBody: PromoteUserRequest,
    ): CancelablePromise<UserResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/users/promote',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get My Equipment
     * Get equipment held by the currently logged-in user.
     * @returns EquipmentResponse Successful Response
     * @throws ApiError
     */
    public static getMyEquipmentUsersMeEquipmentGet(): CancelablePromise<Array<EquipmentResponse>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/users/me/equipment',
        });
    }
    /**
     * Create Equipment
     * @param requestBody
     * @returns EquipmentResponse Successful Response
     * @throws ApiError
     */
    public static createEquipmentEquipmentPost(
        requestBody: EquipmentCreate,
    ): CancelablePromise<EquipmentResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/equipment/',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Assign Owner
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static assignOwnerEquipmentAssignOwnerPost(
        requestBody: AssignOwnerRequest,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/equipment/assign_owner/',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get All Tickets
     * @param status
     * @returns TicketResponse Successful Response
     * @throws ApiError
     */
    public static getAllTicketsTicketsGet(
        status?: (string | null),
    ): CancelablePromise<Array<TicketResponse>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/tickets/',
            query: {
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Unit Readiness
     * @returns UnitReadinessResponse Successful Response
     * @throws ApiError
     */
    public static getUnitReadinessAnalyticsUnitReadinessGet(): CancelablePromise<UnitReadinessResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/analytics/unit_readiness',
        });
    }
}
