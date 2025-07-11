---
name: API Endpoint Request
about: Request a new API endpoint or modification to existing endpoint
title: '[API] '
labels: backend, api
assignees: ''

---

**Endpoint Type**
- [ ] New endpoint
- [ ] Modify existing endpoint
- [ ] Remove endpoint

**HTTP Method**
- [ ] GET
- [ ] POST
- [ ] PUT
- [ ] DELETE
- [ ] PATCH

**Proposed URL Pattern**
```
/api/v1/your-endpoint-here
```

**Purpose**
What would this endpoint be used for?

**Request Body (if applicable)**
```json
{
  "example": "request body"
}
```

**Response Body**
```json
{
  "example": "response body"
}
```

**Authentication Required**
- [ ] No authentication needed
- [ ] Game code validation
- [ ] Host privileges required
- [ ] Player authentication

**Validation Rules**
What validation should be applied to the request?

**Error Scenarios**
What error responses should be returned and when?

**Database Changes**
- [ ] No database changes needed
- [ ] New table/model required
- [ ] Modify existing model
- [ ] New fields needed

**Related Features**
Which parts of the app would use this endpoint?
- [ ] Mobile app (React Native)
- [ ] Web host UI (React)
- [ ] WebSocket functionality
- [ ] Game logic

**Additional Notes**
Any other technical considerations or requirements.