$(function () {
    // 背景画像のプリロード
    $('<img/>').attr('src', 'background-red.jpg').on('load', function() {
        $(this).remove(); // メモリ解放のため要素を削除
    });
    $('<img/>').attr('src', 'background.png').on('load', function() {
        $(this).remove(); // メモリ解放のため要素を削除
    });
    $('<img/>').attr('src', 'title.png').on('load', function() {
        $(this).remove(); // メモリ解放のため要素を削除
    });
    $('<img/>').attr('src', 'header.png').on('load', function() {
        $(this).remove(); // メモリ解放のため要素を削除
    });

    // スキップボタンのクリックイベント
    $(document).on('click', '#skip-button', function() {
        console.log('Skip button clicked');
        skipAnimation();
    });
    
    // アニメーションをスキップする関数
    function skipAnimation() {
        console.log('Skipping animation');
        
        // すべてのアニメーションとタイマーを停止
        $('.animate').stop(true, true);
        $('*').stop(true, true);
        
        // テキスト要素をクリア（タイピングアニメーション停止のため）
        $('#typing-text, #typing-text-2, #typing-text-3, #typing-text-4, #typing-text-final').text('');
        
        // スプラッシュの非表示とメインコンテンツの表示
        $("#splash").fadeOut(500, function() {
            $(this).hide();
            $("#main-content").css({
                "opacity": 1,
                "display": "block"
            });
            
            // メインコンテンツ要素の表示
            $("#unsextetimg").show();
            
            // 固定CTAボタンを表示（SP版のみ）
            if(window.innerWidth < 768) {
                $(".fixed-cta").fadeIn(500);
            }
        });
    }

    // タイピングアニメーション
    function typingAnimation() {
        // 改行を適切な場所に挿入
        const text = "　ある日の暮方の事である。\n\n　一人の下人が、羅生門の下で\n　雨やみを待っていた。広い門\n　の下には、この男のほかに誰\n　もいない。\n\n　ただ、所々丹塗の\n　剥げた、大きな円柱に、\n　蟋蟀が一匹とまっている。\n\n　羅生門が、\n　朱雀大路にある以上は、\n　この男のほかにも、\n　雨やみをする\n　市女笠や揉烏帽子が、\n　もう二三人はありそうなものである。\n\n　それが、\n　この男のほかには\n　誰もいない。";
        
        let i = 0;
        const speed = 25; // ミリ秒ごとに1文字
        
        // HTMLElement直接取得
        const typingElement = document.getElementById('typing-text');
        
        function type() {
            if (i < text.length) {
                // 改行を含めて表示
                typingElement.innerText = text.substring(0, i + 1);
                i++;
                setTimeout(type, speed);
            }
        }
        
        type(); // すぐに開始
    }

    // 2つ目のタイピングアニメーション
    function typingAnimation2() {
        // 句読点で改行したテキスト
        const text = "　下人は七段ある石段の一番上の段に、\n　洗いざらした紺の襖の尻を据えて、\n　右の頬に出来た、\n　大きな面皰を気にしながら、\n　ぼんやり、\n　雨のふるのを眺めていた。";
        
        let i = 0;
        const speed = 45; // ミリ秒ごとに1文字
        
        // HTMLElement直接取得
        const typingElement = document.getElementById('typing-text-2');
        
        function type() {
            if (i < text.length) {
                // 改行を含めて表示
                typingElement.innerText = text.substring(0, i + 1);
                i++;
                setTimeout(type, speed);
            }
        }
        
        type(); // すぐに開始
    }

    // 3つ目のタイピングアニメーション
    function typingAnimation3() {
        // 句読点で改行したテキスト
        const text = "　下人は、\n　太刀を鞘におさめて、\n　その太刀の柄を左の手でおさえながら、\n　冷然として、\n　この話を聞いていた。\n\n　勿論、\n　右の手では、\n　赤く頬に膿を持った大きな面皰を気にしながら、\n　聞いているのである。";
        
        let i = 0;
        const speed = 25; // ミリ秒ごとに1文字
        
        // HTMLElement直接取得
        const typingElement = document.getElementById('typing-text-3');
        
        function type() {
            if (i < text.length) {
                // 改行を含めて表示
                typingElement.innerText = text.substring(0, i + 1);
                i++;
                setTimeout(type, speed);
            }
        }
        
        type(); // すぐに開始
    }

    // 4つ目のタイピングアニメーション
    function typingAnimation4() {
        // 句読点で改行したテキスト
        const text = "　後に残ったのは、\n　ただ、\n　ある仕事をして、\n　それが円満に成就した時の、\n　安らかな得意と\n　満足とがあるばかりである";
        
        let i = 0;
        const speed = 20; // ミリ秒ごとに1文字（やや早め）
        
        // HTMLElement直接取得
        const typingElement = document.getElementById('typing-text-4');
        
        function type() {
            if (i < text.length) {
                // 改行を含めて表示
                typingElement.innerText = text.substring(0, i + 1);
                i++;
                setTimeout(type, speed);
            }
        }
        
        type(); // すぐに開始
    }

    // 最後のタイピングアニメーション
    function typingAnimationFinal() {
        // 句読点で改行したテキスト
        const text = "　老婆はつぶやくような、\n　うめくような声を立てながら、\n　まだ燃えている火の光をたよりに、\n　梯子の口まで、\n　這って行った。\n\n　そうして、\n　そこから、\n　短い白髪を倒さかさまにして、\n　門の下を覗きこんだ。\n\n　外には、\n　ただ、\n　黒洞々たる夜があるばかりである。\n\n　下人の行方は、\n　誰も知らない。";
        
        let i = 0;
        const speed = 40; // ミリ秒ごとに1文字
        
        // HTMLElement直接取得
        const typingElement = document.getElementById('typing-text-final');
        
        function type() {
            if (i < text.length) {
                // 改行を含めて表示
                typingElement.innerText = text.substring(0, i + 1);
                i++;
                setTimeout(type, speed);
            }
        }
        
        type(); // すぐに開始
    }
    
    // メインコンテンツを表示する関数
    function showMainContent() {
        $("#main-content").animate({ opacity: 1 }, 2000);
        
        setTimeout(function() {
            // スプラッシュ画面を非表示
            $("#splash").fadeOut(1000);
            
            setTimeout(function() {
                end_loader();
                
                // メインコンテンツの要素をフェードイン
                $("#unsextetimg").fadeIn(3000);
                $("#headerimg").fadeIn(3000, function() {
                    $(this).css("display", "flex");
                });
            }, 1000);
        }, 1000);
    }

    // スクロールイベント - 背景透明度変更
    $(window).on('scroll', function() {
        if ($(this).scrollTop() > 50) {
            // スクロールが50px以上の場合、背景を暗くする
            $('body').addClass('scrolled');
            
            // 固定CTAボタンを非表示
            $(".fixed-cta").fadeOut(300);
        } else {
            // スクロールが50px未満の場合、背景を元に戻す
            $('body').removeClass('scrolled');
            
            // スプラッシュが非表示の場合のみ、固定CTAボタンを表示（SP版のみ）
            if($("#splash").css("display") === "none" && window.innerWidth < 768) {
                $(".fixed-cta").fadeIn(300);
            }
        }
    });

    function end_loader() {
        $('.popup').slideUp(800);
    }

    // 新しいアニメーションフロー
    function splash_animation() {
        // テキスト要素をクリア
        $('#typing-text').text('');
        $('#typing-text-2').text('');
        $('#typing-text-3').text('');
        $('#typing-text-4').text('');
        $('#typing-text-final').text('');
        
        // 1. 劇団ロゴをふわっと表示
        $("#theater-logo").animate({ opacity: 1 }, 2000, function() {
            // 2. 2秒後にテキストグループを表示
            setTimeout(function() {
                $("#text-animation-group").animate({ opacity: 1 }, 1500, function() {
                    // 3. 4つのテキストアニメーションを開始
                    typingAnimation();
                    typingAnimation2();
                    typingAnimation3();
                    typingAnimation4();
                    
                    // 4. 8秒後にタイトルをフェードアウト
                    setTimeout(function() {
                        $("#splash-title").animate({ opacity: 0 }, 1500, function() {
                            // 5. 最後のテキストをフェードイン
                            $("#typing-text-final").animate({ opacity: 1 }, 1500, function() {
                                // 6. 最後のタイピングアニメーションを開始
                                typingAnimationFinal();
                                
                                // 7. アニメーション開始から3秒後に周りの4つのテキストをフェードアウト
                                setTimeout(function() {
                                    $("#typing-text").animate({ opacity: 0 }, 1500);
                                    $("#typing-text-2").animate({ opacity: 0 }, 1500);
                                    $("#typing-text-3").animate({ opacity: 0 }, 1500);
                                    $("#typing-text-4").animate({ opacity: 0 }, 1500);
                                    
                                    // 8. 最後のテキスト完了から5秒後に最後のテキストをフェードアウト
                                    setTimeout(function() {
                                        $("#typing-text-final").animate({ opacity: 0 }, 1500, function() {
                                            // 9. 劇団ロゴをフェードアウト
                                            $("#theater-logo").animate({ opacity: 0 }, 1500, function() {
                                                // 10. スプラッシュを非表示にしてメインコンテンツを表示
                                                $("#main-content").animate({ opacity: 1 }, 2000);
                                                setTimeout(function() {
                                                    $("#splash").fadeOut(1000);
                                                    
                                                    // ポップアップを閉じる
                                                    setTimeout(function() {
                                                        end_loader();
                                                        
                                                        // メインコンテンツの要素をフェードイン
                                                        $("#unsextetimg").fadeIn(3000);
                                                        
                                                        // 固定CTAボタンを表示（SP版のみ）
                                                        if(window.innerWidth < 768) {
                                                            $(".fixed-cta").fadeIn(1000);
                                                        }
                                                    }, 1000);
                                                }, 1500);
                                            });
                                        });
                                    }, 5000);
                                }, 3000);
                            });
                        });
                    }, 8000);
                });
            }, 2000);
        });
    }
  
    $(window).on('load', function() {
        // スプラッシュアニメーション開始
        splash_animation();
    });
});