document.addEventListener("DOMContentLoaded", function () {
    const flashMessage = document.querySelector(".flash-message");
    if (flashMessage) {
        setTimeout(() => {
            flashMessage.style.display = "none";
        }, 3000);
    }
});
