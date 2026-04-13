// Utility function to get CSRF token from cookies
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');

$(document).ready(function() {
    $(document).on('click', '.protected-link', function(e) {
        e.preventDefault();
        
        var $link = $(this);
        var href = $link.attr('href');
        var correctPin = $link.data('pin') || '11111'; // Default PIN if not set
        
        Swal.fire({
            title: 'Enter 5-digit PIN',
            html: `
                <div class="pin-container">
                    <input type="password" class="pin-input" maxlength="1" autocomplete="off">
                    <input type="password" class="pin-input" maxlength="1" autocomplete="off">
                    <input type="password" class="pin-input" maxlength="1" autocomplete="off">
                    <input type="password" class="pin-input" maxlength="1" autocomplete="off">
                    <input type="password" class="pin-input" maxlength="1" autocomplete="off">
                </div>
            `,
            showCancelButton: true,
            confirmButtonText: 'Verify',
            cancelButtonText: 'Cancel',
            allowOutsideClick: false,
            didOpen: function() {
                var $inputs = $('.pin-input');
                $inputs.val('').first().focus();
                $inputs.off('input keyup paste');
                
                $inputs.on('input keyup', function(e) {
                    var $this = $(this);
                    var value = $this.val();
                    
                    if (value && !/^[a-zA-Z0-9]$/.test(value)) {
                        $this.val('');
                        return;
                    }
                    
                    if (value.length > 1) $this.val(value.slice(0,1));
                    
                    if ($this.val().length === 1) {
                        $this.next('.pin-input').focus();
                    }
                    
                    if (e.key === 'Backspace' && !$this.val()) {
                        $this.prev('.pin-input').focus();
                    }
                    
                    var allFilled = $inputs.toArray().every(input => $(input).val().length === 1);
                    if (allFilled) setTimeout(() => Swal.clickConfirm(), 150);
                });
                
                $inputs.on('paste', function(e) {
                    e.preventDefault();
                    setTimeout(() => {
                        var pasteData = (e.originalEvent.clipboardData || window.clipboardData).getData('text').trim();
                        if (/^[a-zA-Z0-9]{5}$/.test(pasteData)) {
                            $inputs.each((i, input) => $(input).val(pasteData[i] || ''));
                            setTimeout(() => Swal.clickConfirm(), 150);
                        }
                    }, 10);
                });
            },
            preConfirm: function() {
                var pin = $('.pin-input').map(function() {
                    return $(this).val();
                }).get().join('');
                
                if (pin.length !== 5) {
                    Swal.showValidationMessage('Please fill all 5 boxes!');
                    return false;
                }
                
                if (!/^[a-zA-Z0-9]{5}$/.test(pin)) {
                    Swal.showValidationMessage('PIN must contain only letters & numbers!');
                    return false;
                }
                
                return pin; // ✅ This triggers .then()
            }
        }).then(function(result) {
            // ✅ FIXED: Handle all cases properly
            if (result.isConfirmed) {
                var enteredPin = result.value;
                
                if (enteredPin === correctPin) {
                    window.location.href = href;
                } else {
                    Swal.fire({
                        icon: 'error',
                        title: 'Access Denied!',
                        html: '<strong>❌ Wrong PIN!</strong><br>Try again.',
                        timer: 3000,
                        showConfirmButton: false,
                        backdrop: true
                    });
                }
            } else if (result.isDismissed) {
                console.log('User cancelled or closed');
            }
        }).catch(function(error) {
            console.error('Swal error:', error);
        });
    });
});