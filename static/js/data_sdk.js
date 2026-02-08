window.dataSdk = {
    handler: null,
    endpoint: null,

    _getEndpoint: function () {
        const path = window.location.pathname;
        if (path.includes('todo')) return '/api/todos';
        if (path.includes('habit')) return '/api/habits';
        if (path.includes('mood')) return '/api/moods';
        return null;
    },

    init: async function (handler) {
        this.handler = handler;
        this.endpoint = this._getEndpoint();

        if (!this.endpoint) {
            console.warn("DataSDK: No matching endpoint for this page.");
            // Return empty ok to prevent crash
            return { isOk: true };
        }

        try {
            const response = await fetch(this.endpoint);
            if (!response.ok) throw new Error("Failed to fetch data");
            const data = await response.json();

            // Transform data if needed
            // The HTML files expect arrays of objects.

            if (this.handler && this.handler.onDataChanged) {
                this.handler.onDataChanged(data);
            }
            return { isOk: true };
        } catch (e) {
            console.error(e);
            return { isOk: false, error: e };
        }
    },

    create: async function (item) {
        if (!this.endpoint) return { isOk: false };
        try {
            const response = await fetch(this.endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(item)
            });
            if (!response.ok) throw new Error("Failed to create");

            // Refresh data
            await this.init(this.handler);
            return { isOk: true };
        } catch (e) {
            return { isOk: false, error: e };
        }
    },

    update: async function (item) {
        if (!this.endpoint) return { isOk: false };
        // We assume item comes with an ID. The HTML templates use different ID conventions.
        // todo.html: uses __backendId if present, else uses dataset logic. 
        // Actually todo.html's createTaskElement says: row.dataset.taskId = task.__backendId;
        // So the server should return __backendId.

        // However, the item passed to update() is the simple JS object from the `tasks` array.
        // We need to make sure `tasks` array has the IDs.

        // For simplicity, let's assume the API returns objects with `id` and the frontend respects it.
        // CAUTION: todo.html generates `id: Date.now().toString()` on create. 
        // The server should probably respect that or we map it.

        const id = item.id || item.__backendId;

        try {
            const response = await fetch(`${this.endpoint}/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(item)
            });
            if (!response.ok) throw new Error("Failed to update");

            await this.init(this.handler);
            return { isOk: true };
        } catch (e) {
            return { isOk: false, error: e };
        }
    },

    delete: async function (item) {
        if (!this.endpoint) return { isOk: false };
        const id = item.id || item.__backendId;
        try {
            const response = await fetch(`${this.endpoint}/${id}`, {
                method: 'DELETE'
            });
            if (!response.ok) throw new Error("Failed to delete");

            await this.init(this.handler);
            return { isOk: true };
        } catch (e) {
            return { isOk: false, error: e };
        }
    }
};
