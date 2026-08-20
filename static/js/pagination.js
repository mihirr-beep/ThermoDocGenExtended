(function () {
    function buildPageItems(currentPage, totalPages) {
        if (totalPages <= 7) {
            return Array.from({ length: totalPages }, (_, index) => index + 1);
        }

        const pages = new Set([1, 2, 3, 4, 5, totalPages]);
        for (let page = currentPage - 1; page <= currentPage + 1; page += 1) {
            if (page > 1 && page < totalPages) pages.add(page);
        }

        const sortedPages = Array.from(pages).sort((left, right) => left - right);
        const items = [];
        sortedPages.forEach((page, index) => {
            if (index > 0 && page - sortedPages[index - 1] > 1) items.push('...');
            items.push(page);
        });
        return items;
    }

    function renderPagination(container, options) {
        if (!container) return;

        const currentPage = Math.max(1, Number(options.currentPage) || 1);
        const totalPages = Math.max(1, Number(options.totalPages) || 1);
        const goToPage = typeof options.onPageChange === 'function'
            ? options.onPageChange
            : function () {};
        const buttonClass = 'inline-flex h-8 min-w-8 items-center justify-center rounded-md border border-gray-300 bg-white px-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-45';
        const activeClass = 'app-pagination-active';
        const createButton = (label, page, disabled, active, ariaLabel) => {
            const classes = `${buttonClass}${active ? ` ${activeClass}` : ''}`;
            return `<button type="button" class="${classes}" data-pagination-page="${page}" ${disabled ? 'disabled' : ''}${ariaLabel ? ` aria-label="${ariaLabel}"` : ''}>${label}</button>`;
        };

        const parts = [
            createButton('&laquo;', 1, currentPage === 1, false, 'First page'),
            createButton('Previous', currentPage - 1, currentPage === 1, false, 'Previous page')
        ];
        buildPageItems(currentPage, totalPages).forEach((item) => {
            if (item === '...') {
                parts.push('<span class="inline-flex h-8 min-w-8 items-center justify-center px-1 text-sm text-gray-500">...</span>');
            } else {
                parts.push(createButton(String(item), item, false, item === currentPage, `Page ${item}`));
            }
        });
        parts.push(createButton('Next', currentPage + 1, currentPage === totalPages, false, 'Next page'));
        parts.push(createButton('&raquo;', totalPages, currentPage === totalPages, false, 'Last page'));
        container.innerHTML = parts.join('');

        container.querySelectorAll('[data-pagination-page]').forEach((button) => {
            button.addEventListener('click', () => {
                const page = Number(button.dataset.paginationPage);
                if (Number.isFinite(page) && page >= 1 && page <= totalPages && page !== currentPage) {
                    goToPage(page);
                }
            });
        });
    }

    window.AppPagination = { buildPageItems, renderPagination };
}());
